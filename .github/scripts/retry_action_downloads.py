#!/usr/bin/env python3
"""Retry setup-time action download outages, at most three attempts per job.

The workflow_run controller runs after the entire build finishes. It reruns one
matching job (and GitHub's dependent jobs) at a time; the next completion event
can recover another failed job. Successful sibling jobs are not rerun.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import time


MAX_JOB_ATTEMPTS = 3
BUILD_WORKFLOWS = {
    ".github/workflows/release.yml",
    *(f".github/workflows/{platform}.yaml"
      for platform in ("android", "ios", "linux", "macos", "windows")),
}
DOWNLOAD_FAILURE = re.compile(
    r"Failed to download archive 'https://(?:codeload\.github\.com|api\.github\.com)/"
    r"[^'\s]+' after \d+ attempts\.", re.IGNORECASE
)
TRANSIENT_FAILURE = re.compile(
    r"connection attempt failed|connection (?:timed out|reset|refused)|"
    r"forcibly closed|temporary failure in name resolution|"
    r"HttpClient\.Timeout|operation (?:has )?timed out|"
    r"Response status code does not indicate success: (?:408|429|500|502|503|504)\b",
    re.IGNORECASE,
)


class GitHub:
    def __init__(self, repository):
        self.prefix = f"repos/{repository}"

    def request(self, path, method="GET"):
        # Do not automatically repeat a POST: a lost response could still mean
        # the rerun was accepted. A later workflow event rechecks current state.
        result = subprocess.run(
            ["gh", "api", "--method", method, f"{self.prefix}/{path}"],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def pages(self, path, key=None):
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            data = self.request(f"{path}{separator}per_page=100&page={page}")
            items = data[key] if key else data
            yield from items
            if len(items) < 100:
                return
            page += 1


def job_key(job):
    return job["name"], tuple(sorted(job.get("labels", [])))


def failed_during_setup(job):
    if job.get("status") != "completed" or job.get("conclusion") != "failure":
        return False
    steps = job.get("steps", [])
    setup = [step for step in steps
             if step.get("number") == 1 and step.get("name") == "Set up job"
             and step.get("conclusion") == "failure"]
    if len(setup) != 1:
        return False
    return all(
        step is setup[0] or (
            step.get("conclusion") in (None, "skipped")
            and not step.get("started_at")
        )
        for step in steps
    )


def transient_download_failure(annotations):
    failures = "\n".join(
        annotation.get("message", "") for annotation in annotations
        if annotation.get("annotation_level") == "failure"
    )
    return bool(DOWNLOAD_FAILURE.search(failures) and TRANSIENT_FAILURE.search(failures))


def eligible_run(run, repository, expected_attempt):
    return (
        run.get("status") == "completed"
        and run.get("conclusion") == "failure"
        and run.get("run_attempt") == expected_attempt
        and run.get("path", "").split("@", 1)[0] in BUILD_WORKFLOWS
        and run.get("event") in ("workflow_dispatch", "push", "schedule")
        and (run.get("repository") or {}).get("full_name") == repository
        and (run.get("head_repository") or {}).get("full_name") == repository
    )


def report(message):
    print(message)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write(message + "\n\n")


def retry_job(api, repository, run_id, expected_attempt, execute=False, sleep=time.sleep):
    run_path = f"actions/runs/{run_id}"
    run = api.request(run_path)
    if not eligible_run(run, repository, expected_attempt):
        report("No retry: run is unfinished, changed, or outside the build retry policy.")
        return None

    jobs = list(api.pages(f"{run_path}/jobs?filter=latest", "jobs"))
    candidates = [job for job in jobs if failed_during_setup(job)]
    if not candidates:
        report("No retry: no jobs failed before their build steps.")
        return None

    # Count actual executions, not the run-wide attempt number. Retrying Windows
    # must not consume the retry allowance of a separate macOS setup failure.
    history = {
        job["id"]: job
        for job in api.pages(f"{run_path}/jobs?filter=all", "jobs")
    }
    attempts = Counter(job_key(job) for job in history.values())
    for job in sorted(candidates, key=lambda value: value["id"]):
        count = attempts[job_key(job)]
        if count == 0 or count >= MAX_JOB_ATTEMPTS:
            report(f"Skipping job {job['id']}: {count} recorded attempts (limit {MAX_JOB_ATTEMPTS}).")
            continue
        # Construct the endpoint from the known repository and numeric check ID;
        # never follow an arbitrary URL from a triggering run.
        check_id = int(job["check_run_url"].rstrip("/").rsplit("/", 1)[-1])
        annotations = api.pages(f"check-runs/{check_id}/annotations")
        if not transient_download_failure(annotations):
            report(f"Skipping job {job['id']}: no transient action-download failure.")
            continue

        if not execute:
            report(f"Would retry job {job['id']} (attempt {count + 1}/{MAX_JOB_ATTEMPTS}).")
            return job["id"]

        # Allow the download endpoint to recover, then protect against a manual
        # rerun/cancellation or another controller changing the run meanwhile.
        sleep(30)
        if not eligible_run(api.request(run_path), repository, expected_attempt):
            report("No retry: run changed during the retry delay.")
            return None
        api.request(f"actions/jobs/{job['id']}/rerun", method="POST")
        report(f"Requested retry of job {job['id']} (attempt {count + 1}/{MAX_JOB_ATTEMPTS}).")
        return job["id"]

    report("No eligible action-download failures remain.")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--expected-attempt", type=int, required=True)
    parser.add_argument("--execute", action="store_true", help="Submit the retry; otherwise only inspect.")
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    retry_job(GitHub(repository), repository, args.run_id, args.expected_attempt, args.execute)


if __name__ == "__main__":
    main()
