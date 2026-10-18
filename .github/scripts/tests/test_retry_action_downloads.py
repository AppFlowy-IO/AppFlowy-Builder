#!/usr/bin/env python3
"""Exercise retry decisions and API effects using the reported setup failure."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock


SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("retry_action_downloads", SCRIPTS / "retry_action_downloads.py")
retry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retry)
FIXTURE = json.loads((Path(__file__).parent / "fixtures/action_download_timeout.json").read_text())
REPOSITORY = "AppFlowy-IO/AppFlowy-Builder"
RUN_ID = 39089398788


class FakeGitHub:
    def __init__(self):
        self.run = {
            "status": "completed", "conclusion": "failure", "run_attempt": 1,
            "path": ".github/workflows/release.yml", "event": "workflow_dispatch",
            "repository": {"full_name": REPOSITORY},
            "head_repository": {"full_name": REPOSITORY},
        }
        self.jobs = [copy.deepcopy(FIXTURE["job"])]
        self.history = copy.deepcopy(self.jobs)
        self.annotations = copy.deepcopy(FIXTURE["annotations"])
        self.posts = []
        self.post_error = None

    def request(self, path, method="GET"):
        if method == "POST":
            self.posts.append(path)
            if self.post_error:
                raise self.post_error
            return None
        assert path == f"actions/runs/{RUN_ID}", path
        return copy.deepcopy(self.run)

    def pages(self, path, key=None):
        if path == f"actions/runs/{RUN_ID}/jobs?filter=latest":
            assert key == "jobs"
            return iter(self.jobs)
        if path == f"actions/runs/{RUN_ID}/jobs?filter=all":
            assert key == "jobs"
            return iter(self.history)
        assert path.startswith("check-runs/") and path.endswith("/annotations"), path
        return iter(self.annotations)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.sleep = Mock()

    def run_retry(self, execute=True, expected_attempt=1):
        return retry.retry_job(
            self.api, REPOSITORY, RUN_ID, expected_attempt,
            execute=execute, sleep=self.sleep,
        )

    def test_reported_timeout_retries_only_windows_despite_other_failures(self):
        self.api.jobs += [
            {"id": 2, "name": "Build Android", "status": "completed", "conclusion": "success"},
            {
                "id": 3, "name": "Build macOS", "status": "completed", "conclusion": "failure",
                "steps": [{"number": 1, "name": "Set up job", "conclusion": "success"}],
            },
        ]
        self.assertEqual(self.run_retry(), FIXTURE["job"]["id"])
        self.assertEqual(self.api.posts, ["actions/jobs/117562773270/rerun"])
        self.sleep.assert_called_once_with(30)

    def test_transient_http_and_timeout_signatures(self):
        archive = next(a for a in self.api.annotations if a["message"].startswith("Failed to download archive"))
        for reason in (
            "Response status code does not indicate success: 503 (Service Unavailable).",
            "Response status code does not indicate success: 429 (Too Many Requests).",
            "The request was canceled due to the configured HttpClient.Timeout of 100 seconds elapsing.",
        ):
            with self.subTest(reason=reason):
                self.api.annotations = [archive, {"annotation_level": "failure", "message": reason}]
                self.assertEqual(self.run_retry(execute=False), FIXTURE["job"]["id"])
        self.assertEqual(self.api.posts, [])

    def test_permanent_download_errors_and_runner_shutdown_do_not_retry(self):
        archive = next(a for a in self.api.annotations if a["message"].startswith("Failed to download archive"))
        for reason in (
            "Response status code does not indicate success: 401 (Unauthorized).",
            "Response status code does not indicate success: 404 (Not Found).",
            "Unable to resolve action, unable to find version",
            "The runner has received a shutdown signal. Process completed with exit code 143.",
        ):
            with self.subTest(reason=reason):
                self.api.annotations = [archive, {"annotation_level": "failure", "message": reason}]
                self.assertIsNone(self.run_retry())
        self.assertEqual(self.api.posts, [])
        self.sleep.assert_not_called()

    def test_earlier_network_warning_does_not_mask_final_auth_failure(self):
        for annotation in self.api.annotations:
            if annotation["annotation_level"] == "failure" and "connection attempt" in annotation["message"]:
                annotation["message"] = "Response status code does not indicate success: 401 (Unauthorized)."
        self.assertIsNone(self.run_retry())
        self.assertEqual(self.api.posts, [])

    def test_build_steps_must_not_have_started(self):
        self.api.jobs[0]["steps"].append({
            "number": 2, "name": "Build", "conclusion": "failure",
            "started_at": "2026-10-18T02:15:30Z",
        })
        self.assertIsNone(self.run_retry())
        self.assertEqual(self.api.posts, [])

    def test_missing_download_failure_is_not_enough(self):
        self.api.annotations = [{"annotation_level": "failure", "message": "connection attempt failed"}]
        self.assertIsNone(self.run_retry())
        self.assertEqual(self.api.posts, [])

    def test_retry_limit_counts_job_executions(self):
        self.api.history = [
            dict(FIXTURE["job"], id=100 + attempt, run_attempt=attempt)
            for attempt in range(1, 4)
        ]
        self.api.run["run_attempt"] = 3
        self.assertIsNone(self.run_retry(expected_attempt=3))
        self.assertEqual(self.api.posts, [])

    def test_other_job_retries_do_not_exhaust_windows_allowance(self):
        self.api.run["run_attempt"] = 4
        self.api.history += [
            dict(FIXTURE["job"], id=100 + attempt, name="Build macOS", run_attempt=attempt)
            for attempt in range(1, 4)
        ]
        self.assertEqual(self.run_retry(expected_attempt=4), FIXTURE["job"]["id"])

    def test_can_move_to_another_job_after_first_reaches_limit(self):
        first = self.api.jobs[0]
        second = dict(first, id=first["id"] + 1, name="Build macOS")
        self.api.jobs.append(second)
        self.api.history += [dict(first, id=1), dict(first, id=2), second]
        self.assertEqual(self.run_retry(), second["id"])
        self.assertEqual(self.api.posts, [f"actions/jobs/{second['id']}/rerun"])

    def test_stale_cancelled_running_or_untrusted_runs_do_not_retry(self):
        original = copy.deepcopy(self.api.run)
        for change in (
            {"run_attempt": 2}, {"status": "in_progress"}, {"conclusion": "cancelled"},
            {"event": "pull_request"}, {"path": ".github/workflows/auto_update.yaml"},
            {"repository": {"full_name": "someone/fork"}},
            {"head_repository": {"full_name": "someone/fork"}},
            {"head_repository": None},
        ):
            with self.subTest(change=change):
                self.api.run = {**original, **change}
                self.assertIsNone(self.run_retry())
        self.assertEqual(self.api.posts, [])

    def test_manual_rerun_during_delay_is_not_duplicated(self):
        self.sleep.side_effect = lambda _: self.api.run.update(run_attempt=2, status="queued")
        self.assertIsNone(self.run_retry())
        self.assertEqual(self.api.posts, [])

    def test_dry_run_never_posts_or_waits(self):
        self.assertEqual(self.run_retry(execute=False), FIXTURE["job"]["id"])
        self.assertEqual(self.api.posts, [])
        self.sleep.assert_not_called()

    def test_ambiguous_post_failure_is_not_repeated(self):
        self.api.post_error = subprocess.CalledProcessError(1, "gh")
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_retry()
        self.assertEqual(self.api.posts, ["actions/jobs/117562773270/rerun"])

    def test_history_and_annotation_pagination(self):
        for path, key in (("actions/runs/1/jobs?filter=all", "jobs"), ("check-runs/1/annotations", None)):
            with self.subTest(path=path):
                api = retry.GitHub(REPOSITORY)
                first, second = list(range(100)), [100]
                api.request = Mock(side_effect=[{key: first}, {key: second}] if key else [first, second])
                self.assertEqual(list(api.pages(path, key)), list(range(101)))
                separator = "&" if "?" in path else "?"
                self.assertEqual(
                    [call.args[0] for call in api.request.call_args_list],
                    [f"{path}{separator}per_page=100&page=1", f"{path}{separator}per_page=100&page=2"],
                )


if __name__ == "__main__":
    unittest.main()
