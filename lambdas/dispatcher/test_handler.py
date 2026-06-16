"""
Local tests for the dispatcher Lambda — no AWS needed.

Stubs boto3 so we can assert exactly what RunTask is called with, that the job
is passed through as the SCAN_JOB override, and that failures are reported per
message via batchItemFailures (so SQS only retries the bad ones).

Run:  python3 test_handler.py
"""

import json
import sys
import types


class _FakeECS:
    def __init__(self):
        self.calls = []
        self.next_response = None
        self.raise_exc = None

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise self.raise_exc
        return self.next_response or {
            "tasks": [{"taskArn": "arn:aws:ecs:us-east-1:111:task/abc123"}],
            "failures": [],
        }


_fake_ecs = _FakeECS()

fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = lambda *a, **k: _fake_ecs
sys.modules["boto3"] = fake_boto3

import os
os.environ["CLUSTER_ARN"] = "arn:aws:ecs:us-east-1:111:cluster/test"
os.environ["TASK_DEFINITION_ARN"] = "arn:aws:ecs:us-east-1:111:task-definition/scanner:1"
os.environ["SUBNET_IDS"] = "subnet-aaa,subnet-bbb"
os.environ["SECURITY_GROUP_ID"] = "sg-123"

import handler

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(cond)
    print(f"  [{PASS if cond else FAIL}] {name}")


def _job(pr_number=42):
    return {
        "repo": {"full_name": "octo/demo", "clone_url": "https://github.com/octo/demo.git"},
        "pull_request": {"number": pr_number, "head_sha": "a" * 40,
                         "comments_url": "https://api.github.com/x"},
    }


def _sqs_event(jobs):
    return {"Records": [{"messageId": f"m{i}", "body": json.dumps(j)} for i, j in enumerate(jobs)]}


def test_single_job_launches_task():
    print("one job launches one Fargate task")
    _fake_ecs.calls.clear()
    _fake_ecs.raise_exc = None
    out = handler.handler(_sqs_event([_job()]), None)
    check("called run_task once", len(_fake_ecs.calls) == 1)
    check("no batch failures", out["batchItemFailures"] == [])
    call = _fake_ecs.calls[0]
    check("launch type FARGATE", call["launchType"] == "FARGATE")
    check("uses both subnets", call["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["subnet-aaa", "subnet-bbb"])
    check("no public IP", call["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "DISABLED")


def test_job_passed_as_scan_job_override():
    print("job is passed to container as SCAN_JOB env override")
    _fake_ecs.calls.clear()
    handler.handler(_sqs_event([_job(99)]), None)
    overrides = _fake_ecs.calls[0]["overrides"]["containerOverrides"][0]
    check("targets scanner container", overrides["name"] == "scanner")
    env = {e["name"]: e["value"] for e in overrides["environment"]}
    check("SCAN_JOB present", "SCAN_JOB" in env)
    parsed = json.loads(env["SCAN_JOB"])
    check("SCAN_JOB carries PR number", parsed["pull_request"]["number"] == 99)
    check("SCAN_JOB carries clone_url", parsed["repo"]["clone_url"].endswith(".git"))


def test_batch_launches_each():
    print("batch of 3 launches 3 tasks")
    _fake_ecs.calls.clear()
    out = handler.handler(_sqs_event([_job(1), _job(2), _job(3)]), None)
    check("3 run_task calls", len(_fake_ecs.calls) == 3)
    check("no failures", out["batchItemFailures"] == [])


def test_runtask_failure_reports_item():
    print("RunTask exception reports that message as failed")
    _fake_ecs.calls.clear()
    _fake_ecs.raise_exc = RuntimeError("boom")
    out = handler.handler(_sqs_event([_job()]), None)
    check("one batch failure reported", len(out["batchItemFailures"]) == 1)
    check("failure has itemIdentifier", out["batchItemFailures"][0]["itemIdentifier"] == "m0")
    _fake_ecs.raise_exc = None


def test_runtask_failures_field_raises():
    print("RunTask 'failures' in response is treated as failure")
    _fake_ecs.calls.clear()
    _fake_ecs.raise_exc = None
    _fake_ecs.next_response = {"tasks": [], "failures": [{"reason": "capacity"}]}
    out = handler.handler(_sqs_event([_job()]), None)
    check("reported as batch failure", len(out["batchItemFailures"]) == 1)
    _fake_ecs.next_response = None


def test_bad_json_reports_failure():
    print("malformed message body is reported, not crash")
    _fake_ecs.calls.clear()
    event = {"Records": [{"messageId": "bad1", "body": "{not json"}]}
    out = handler.handler(event, None)
    check("no task launched", len(_fake_ecs.calls) == 0)
    check("reported as failure", out["batchItemFailures"][0]["itemIdentifier"] == "bad1")


if __name__ == "__main__":
    for t in [
        test_single_job_launches_task,
        test_job_passed_as_scan_job_override,
        test_batch_launches_each,
        test_runtask_failure_reports_item,
        test_runtask_failures_field_raises,
        test_bad_json_reports_failure,
    ]:
        t()
        print()
    total, passed = len(results), sum(results)
    print("=" * 40)
    print(f"{passed}/{total} assertions passed")
    sys.exit(0 if passed == total else 1)
