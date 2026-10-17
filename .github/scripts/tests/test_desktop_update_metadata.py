#!/usr/bin/env python3
"""Run Builder's real manifest steps against a supplied Desktop source policy."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[3]
GENERATOR = Path("scripts/tool/generate_desktop_update_manifest.py")
POLICY = Path("rust-lib/flowy-user/resources/desktop_server_compatibility.json")
STEP_NAME = "Generate desktop update manifest"
WORKFLOWS = {
    "release.yml": "prepare",
    "macos.yaml": "universal",
    "windows.yaml": "build",
    "linux.yaml": "prepare",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def one_step(job, predicate):
    matches = [step for step in job["steps"] if predicate(step)]
    require(len(matches) == 1, f"Expected one matching step, found {len(matches)}")
    return matches[0]


def version_tuple(value):
    return tuple(map(int, value.split(".")))


def run_generation(frontend, step, version, omitted=None):
    with tempfile.TemporaryDirectory(prefix="appflowy-update-metadata-") as directory:
        root = Path(directory)
        for relative in (GENERATOR, POLICY):
            if relative != omitted:
                target = root / "frontend" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(frontend / relative, target)
        # Match setup-python's `python` command without relying on the host default.
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").symlink_to(sys.executable)
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=root,
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                 "RELEASE_VERSION": version},
            capture_output=True, text=True, timeout=15, check=False,
        )
        output = root / "desktop-update.json"
        require(not (root / "injected").exists(), "Release version executed as shell code")
        data = json.loads(output.read_text()) if output.exists() else None
        return result, data


def check_generation(frontend, name, step, policy):
    require(step.get("shell") == "bash", f"{name}: explicit bash is required on Windows")
    expected_env = ("${{ steps.parse_inputs.outputs.version }}" if name == "release.yml"
                    else "${{ inputs.build_name }}")
    require(step.get("env", {}).get("RELEASE_VERSION") == expected_env,
            f"{name}: version must be passed through the environment")
    require("${{" not in step["run"], f"{name}: shell must not interpolate workflow input")
    reviewed = policy["reviewed_through_client_version"]
    expected_floor = [row["min_server"] for row in policy["rows"]
                      if version_tuple(row["client_from"]) <= version_tuple(reviewed)][-1]
    result, data = run_generation(frontend, step, reviewed)
    require(result.returncode == 0, f"{name}: {result.stdout}\n{result.stderr}")
    require(data == {"version": reviewed, "minimum_server_version": expected_floor},
            f"{name}: wrong manifest contents: {data}")

    major, minor, patch = version_tuple(reviewed)
    cases = [
        ("missing generator", reviewed, GENERATOR, "Desktop source does not contain"),
        ("missing policy", reviewed, POLICY, POLICY.name),
        ("unreviewed", f"{major}.{minor}.{patch + 1}", None, "Review the desktop/server"),
        ("prerelease", f"{reviewed}-rc.1", None, "Expected a stable semantic version"),
        ("shell input", f"{reviewed}$(touch injected)", None,
         "Expected a stable semantic version"),
    ]
    for case, version, omitted, error in cases:
        result, data = run_generation(frontend, step, version, omitted)
        require(result.returncode != 0 and data is None,
                f"{name}/{case}: unsafe input produced metadata: {data}")
        require(error in result.stdout + result.stderr,
                f"{name}/{case}: missing diagnostic: {result.stdout}\n{result.stderr}")
    print(f"PASS {name}: generation and all five failure controls")


def check_artifact(job, standalone):
    step = one_step(job, lambda value: value.get("uses", "").startswith("actions/upload-artifact@")
                    and value.get("with", {}).get("name") == "desktop-update.json")
    require(step["with"].get("path") == "desktop-update.json", "Wrong manifest artifact path")
    require(step["with"].get("if-no-files-found") == "error", "Missing manifest must fail CI")
    if standalone:
        require(step.get("if") == "inputs.upload_url == ''", "Reusable jobs must avoid duplicate artifacts")
    return step


def check_source_resolution(prepare):
    require(prepare["outputs"].get("source_sha") == "${{ steps.source.outputs.sha }}",
            "Prepare must expose the resolved source commit")
    resolver = one_step(prepare, lambda step: step.get("id") == "source")
    require("git rev-parse HEAD" in resolver["run"], "Resolve the actual checked-out commit")
    checkout = one_step(prepare, lambda step: step.get("name") == "Checkout source code")
    generation = one_step(prepare, lambda step: step.get("name") == STEP_NAME)
    steps = prepare["steps"]
    require(steps.index(checkout) < steps.index(resolver) < steps.index(generation),
            "Resolve source after checkout and before generating its metadata")


def check_parent_publication(release):
    prepare = release["prepare"]
    check_source_resolution(prepare)
    callers = [job for job in release.values() if "uses" in job]
    require(len(callers) == 6, "Check all six platform calls")
    for job in callers:
        require(job["with"]["branch"] == "${{ needs.prepare.outputs.source_sha }}",
                "Every platform must build the metadata's source commit")
        require(job["needs"] == "prepare", "Platform jobs must wait for manifest publication")

    generation = one_step(prepare, lambda step: step.get("name") == STEP_NAME)
    condition = generation.get("if", "")
    selection = re.search(r"contains\(fromJSON\('([^']+)'\), steps\.parse_inputs\.outputs\.platforms\)", condition)
    require(selection is not None, "Use an exact desktop platform selection gate")
    selected = set(json.loads(selection.group(1)))
    require(selected == {"all", "desktop", "macos", "windows", "linux"},
            "Manifest generation must be gated to desktop platform selections")
    require("steps.parse_inputs.outputs.skip_build != 'true'" in condition,
            "Skipped builds must not generate metadata")
    artifact = check_artifact(prepare, standalone=False)
    require(artifact.get("if") == condition, "Artifact must use the desktop-only condition")
    create = one_step(prepare, lambda step: step.get("id") == "create_release")
    upload = one_step(prepare, lambda step: step.get("uses", "").startswith("actions/upload-release-asset@"))
    require(upload.get("if") == condition, "Release asset must use the desktop-only condition")
    require(upload["with"].get("asset_path") == "desktop-update.json"
            and upload["with"].get("asset_name") == "desktop-update.json",
            "Release asset must retain its public filename")
    require(upload["with"].get("upload_url") == "${{ steps.create_release.outputs.upload_url }}",
            "Upload metadata into this build's release")
    steps = prepare["steps"]
    require(steps.index(generation) < steps.index(create) < steps.index(upload),
            "Validate metadata before creating the release, then attach it before building")


def check_linux_contracts(linux):
    check_source_resolution(linux["prepare"])
    checkout = one_step(linux["build"], lambda step: step.get("name") == "Checkout source code")
    require(checkout["with"]["ref"] == "${{ needs.prepare.outputs.source_sha }}",
            "Both Linux architectures must build the metadata's source commit")
    require(linux["build"]["needs"] == "prepare", "Linux build must wait for metadata validation")
    require(linux["publish-linux"]["needs"] == ["build", "archive-smoke-test", "deb-install-test"],
            "Manifest changes must preserve all Linux publishing gates")


def check_workflow_contracts(workflows):
    check_parent_publication(workflows["release.yml"]["jobs"])
    check_linux_contracts(workflows["linux.yaml"]["jobs"])
    for filename, job_name in WORKFLOWS.items():
        job = workflows[filename]["jobs"][job_name]
        checkout = one_step(job, lambda step: step.get("name") == "Checkout source code")
        generation = one_step(job, lambda step: step.get("name") == STEP_NAME)
        require(job["steps"].index(checkout) < job["steps"].index(generation),
                f"{filename}: generation must follow source checkout")
        if filename != "release.yml":
            check_artifact(job, standalone=True)
    print("PASS workflow contracts: source pinning, desktop selection, artifact ownership, Linux gates")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", required=True, type=Path)
    frontend = parser.parse_args().frontend.resolve()
    for relative in (GENERATOR, POLICY):
        require((frontend / relative).is_file(), f"Required Desktop source missing: {frontend / relative}")
    policy = json.loads((frontend / POLICY).read_text())
    workflows = {name: yaml.safe_load((ROOT / ".github/workflows" / name).read_text()) for name in WORKFLOWS}
    for name, job_name in WORKFLOWS.items():
        step = one_step(workflows[name]["jobs"][job_name], lambda value: value.get("name") == STEP_NAME)
        check_generation(frontend, name, step, policy)
    check_workflow_contracts(workflows)


if __name__ == "__main__":
    main()
