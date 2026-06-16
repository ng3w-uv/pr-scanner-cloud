"""
Local tests for the webhook Lambda — no AWS account or network needed.

We stub `boto3` in sys.modules BEFORE importing the handler, so the module-level
boto3.client(...) calls return fakes. This lets us exercise the real HMAC logic
and event-routing branches and assert exactly what gets enqueued to SQS.

Run:  python3 test_handler.py
"""

import hashlib
import hmac
import json
import sys
import types

SECRET = "it's-a-secret-to-everybody"


class _FakeSQS:
    def __init__(self):
        self.sent = []

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "fake-message-id"}


class _FakeSecrets:
    def __init__(self, secret):
        self._secret = secret

    def get_secret_value(self, **kwargs):
        return {"SecretString": self._secret}


_fake_sqs = _FakeSQS()
_fake_secrets = _FakeSecrets(SECRET)


def _fake_client(service_name, *args, **kwargs):
    if service_name == "sqs":
        return _fake_sqs
    if service_name == "secretsmanager":
        return _fake_secrets
    raise ValueError(f"unexpected client: {service_name}")


fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = _fake_client
sys.modules["boto3"] = fake_boto3

fake_exceptions = types.ModuleType("botocore.exceptions")
class ClientError(Exception):
    pass
fake_exceptions.ClientError = ClientError
fake_botocore = types.ModuleType("botocore")
fake_botocore.exceptions = fake_exceptions
sys.modules["botocore"] = fake_botocore
sys.modules["botocore.exceptions"] = fake_exceptions

import os
os.environ["JOB_QUEUE_URL"] = "https://sqs.fake/123/jobs"
os.environ["WEBHOOK_SECRET_ARN"] = "arn:aws:secretsmanager:fake"

import handler


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _pr_payload(action="opened"):
    return {
        "action": action,
        "repository": {
            "full_name": "octo/demo",
            "name": "demo",
            "owner": {"login": "octo"},
            "clone_url": "https://github.com/octo/demo.git",
            "default_branch": "main",
        },
        "pull_request": {
            "number": 42,
            "title": "Add login endpoint",
            "head": {"ref": "feature/login", "sha": "a" * 40},
            "base": {"ref": "main", "sha": "b" * 40},
            "diff_url": "https://github.com/octo/demo/pull/42.diff",
            "comments_url": "https://api.github.com/repos/octo/demo/issues/42/comments",
            "statuses_url": "https://api.github.com/repos/octo/demo/statuses/" + "a" * 40,
        },
        "installation": {"id": 9001},
    }


def _event(raw: bytes, signature: str, gh_event: str = "pull_request"):
    return {
        "headers": {
            "x-hub-signature-256": signature,
            "x-github-event": gh_event,
            "content-type": "application/json",
        },
        "body": raw.decode("utf-8"),
        "isBase64Encoded": False,
    }


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(cond)
    print(f"  [{PASS if cond else FAIL}] {name}")


def test_valid_signature_enqueues():
    print("valid PR webhook with correct signature")
    _fake_sqs.sent.clear()
    raw = json.dumps(_pr_payload("opened")).encode()
    resp = handler.handler(_event(raw, _sign(raw)), None)
    check("returns 202", resp["statusCode"] == 202)
    check("enqueued exactly one message", len(_fake_sqs.sent) == 1)
    if _fake_sqs.sent:
        body = json.loads(_fake_sqs.sent[0]["MessageBody"])
        check("message carries PR number 42", body["pull_request"]["number"] == 42)
        check("message carries head sha", body["pull_request"]["head_sha"] == "a" * 40)
        check("message carries diff_url", body["pull_request"]["diff_url"].endswith("42.diff"))
        check("message carries installation id", body["installation_id"] == 9001)
        check("message carries clone_url", body["repo"]["clone_url"].endswith(".git"))


def test_bad_signature_rejected():
    print("tampered body / wrong signature")
    _fake_sqs.sent.clear()
    raw = json.dumps(_pr_payload("opened")).encode()
    bad_sig = _sign(raw, secret="wrong-secret")
    resp = handler.handler(_event(raw, bad_sig), None)
    check("returns 401", resp["statusCode"] == 401)
    check("nothing enqueued", len(_fake_sqs.sent) == 0)


def test_tampered_body_rejected():
    print("valid signature but body changed after signing")
    _fake_sqs.sent.clear()
    raw = json.dumps(_pr_payload("opened")).encode()
    sig = _sign(raw)
    tampered = json.dumps(_pr_payload("opened") | {"injected": "evil"}).encode()
    resp = handler.handler(_event(tampered, sig), None)
    check("returns 401", resp["statusCode"] == 401)
    check("nothing enqueued", len(_fake_sqs.sent) == 0)


def test_ignored_action():
    print("PR action we don't scan (e.g. labeled)")
    _fake_sqs.sent.clear()
    raw = json.dumps(_pr_payload("labeled")).encode()
    resp = handler.handler(_event(raw, _sign(raw)), None)
    check("returns 204", resp["statusCode"] == 204)
    check("nothing enqueued", len(_fake_sqs.sent) == 0)


def test_ping_event():
    print("GitHub ping event on webhook creation")
    _fake_sqs.sent.clear()
    raw = b"{}"
    resp = handler.handler(_event(raw, "", gh_event="ping"), None)
    check("returns 200", resp["statusCode"] == 200)
    check("nothing enqueued", len(_fake_sqs.sent) == 0)


def test_non_pr_event_after_valid_sig():
    print("valid signature but event is 'push', not 'pull_request'")
    _fake_sqs.sent.clear()
    raw = json.dumps({"ref": "refs/heads/main"}).encode()
    resp = handler.handler(_event(raw, _sign(raw), gh_event="push"), None)
    check("returns 204", resp["statusCode"] == 204)
    check("nothing enqueued", len(_fake_sqs.sent) == 0)


if __name__ == "__main__":
    for t in [
        test_valid_signature_enqueues,
        test_bad_signature_rejected,
        test_tampered_body_rejected,
        test_ignored_action,
        test_ping_event,
        test_non_pr_event_after_valid_sig,
    ]:
        t()
        print()

    total, passed = len(results), sum(results)
    print(f"{'=' * 40}")
    print(f"{passed}/{total} assertions passed")
    sys.exit(0 if passed == total else 1)
