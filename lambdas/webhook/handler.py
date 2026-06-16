"""
PR Scanner — webhook Lambda.

Entry point for GitHub PR webhooks. Exposed via a Lambda Function URL.

Responsibilities (and nothing more):
  1. Verify the GitHub HMAC-SHA256 signature against the secret in Secrets Manager.
  2. Filter to the PR events we actually scan (opened / synchronize / reopened).
  3. Build a self-contained job message and enqueue it to SQS.
  4. Return fast so GitHub's webhook delivery doesn't time out.

The actual scan is launched downstream by the dispatcher Lambda. This function
never calls ECS and never touches the PR code.

Standard library + boto3 only (boto3 ships in the Lambda runtime), so this packages
as a plain .py zip with no dependency layer.
"""

import hashlib
import hmac
import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

QUEUE_URL = os.environ["JOB_QUEUE_URL"]
SECRET_ARN = os.environ["WEBHOOK_SECRET_ARN"]
SIGNATURE_HEADER = "x-hub-signature-256"
EVENT_HEADER = "x-github-event"
SCANNABLE_ACTIONS = {"opened", "synchronize", "reopened"}

_sqs = boto3.client("sqs")
_secrets = boto3.client("secretsmanager")

_secret_cache = {"value": None, "fetched_at": 0.0}
_SECRET_TTL_SECONDS = 300


def _get_webhook_secret():
    """Fetch the webhook secret, cached across warm invocations to cut Secrets Manager calls."""
    now = time.time()
    if _secret_cache["value"] is not None and (now - _secret_cache["fetched_at"]) < _SECRET_TTL_SECONDS:
        return _secret_cache["value"]

    resp = _secrets.get_secret_value(SecretId=SECRET_ARN)
    secret = resp["SecretString"]
    _secret_cache["value"] = secret
    _secret_cache["fetched_at"] = now
    return secret


def _verify_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """
    Constant-time HMAC-SHA256 verification of the GitHub webhook payload.

    GitHub sends the header as 'sha256=<hexdigest>' computed over the raw request
    body using the shared secret. We recompute and compare with compare_digest to
    avoid timing side channels.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


def _normalize_headers(event: dict) -> dict:
    """Lambda Function URL headers are already lowercased, but normalize defensively."""
    headers = event.get("headers") or {}
    return {k.lower(): v for k, v in headers.items()}


def _get_raw_body(event: dict) -> bytes:
    """Return the raw request body bytes exactly as GitHub sent them (signature is over these)."""
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        return base64.b64decode(body)
    return body.encode("utf-8")


def _build_job_message(payload: dict) -> dict:
    """
    Build the self-contained job message the dispatcher and Fargate task consume.

    'Full' message body: enough for the scanner to clone the diff and post back
    without re-deriving anything from the webhook.
    """
    pr = payload["pull_request"]
    repo = payload["repository"]
    head = pr["head"]
    base = pr["base"]

    return {
        "schema_version": 1,
        "action": payload["action"],
        "repo": {
            "full_name": repo["full_name"],
            "owner": repo["owner"]["login"],
            "name": repo["name"],
            "clone_url": repo["clone_url"],
            "default_branch": repo["default_branch"],
        },
        "pull_request": {
            "number": pr["number"],
            "title": pr["title"],
            "head_branch": head["ref"],
            "head_sha": head["sha"],
            "base_branch": base["ref"],
            "base_sha": base["sha"],
            "diff_url": pr["diff_url"],
            "comments_url": pr["comments_url"],
            "statuses_url": pr["statuses_url"],
        },
        "installation_id": (payload.get("installation") or {}).get("id"),
        "received_at": int(time.time()),
    }


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    headers = _normalize_headers(event)
    raw_body = _get_raw_body(event)

    github_event = headers.get(EVENT_HEADER, "")

    if github_event == "ping":
        logger.info("Received GitHub ping event")
        return _response(200, {"message": "pong"})

    try:
        secret = _get_webhook_secret()
    except ClientError:
        logger.exception("Failed to retrieve webhook secret")
        return _response(500, {"error": "secret_unavailable"})

    if not _verify_signature(raw_body, headers.get(SIGNATURE_HEADER, ""), secret):
        logger.warning("HMAC signature verification failed")
        return _response(401, {"error": "invalid_signature"})

    if github_event != "pull_request":
        logger.info("Ignoring non-PR event: %s", github_event)
        return _response(204, {"message": "ignored_event"})

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Body passed HMAC but is not valid JSON")
        return _response(400, {"error": "invalid_json"})

    action = payload.get("action")
    if action not in SCANNABLE_ACTIONS:
        logger.info("Ignoring PR action: %s", action)
        return _response(204, {"message": "ignored_action", "action": action})

    try:
        job = _build_job_message(payload)
    except KeyError:
        logger.exception("PR payload missing expected fields")
        return _response(400, {"error": "malformed_payload"})

    try:
        _sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(job),
            MessageAttributes={
                "repo": {"DataType": "String", "StringValue": job["repo"]["full_name"]},
                "pr_number": {"DataType": "Number", "StringValue": str(job["pull_request"]["number"])},
            },
        )
    except ClientError:
        logger.exception("Failed to enqueue scan job")
        return _response(502, {"error": "enqueue_failed"})

    logger.info(
        "Enqueued scan job repo=%s pr=#%s sha=%s",
        job["repo"]["full_name"],
        job["pull_request"]["number"],
        job["pull_request"]["head_sha"][:8],
    )
    return _response(202, {
        "message": "scan_enqueued",
        "repo": job["repo"]["full_name"],
        "pr_number": job["pull_request"]["number"],
    })
