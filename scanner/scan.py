"""
PR Scanner — scan task (runs inside the Fargate container).

Lifecycle:
  1. Read the job (PR metadata) from the SCAN_JOB env var (JSON the dispatcher passes in).
  2. Shallow-clone the PR repo at the head SHA.
  3. Run Semgrep over the checkout, producing JSON findings.
  4. Upload the full report to S3 and generate a pre-signed link.
  5. Write a metadata row to RDS (status, finding counts, S3 key).
  6. Post a pass/fail comment back to the PR.

Failure handling: if anything in steps 2-5 fails, we still try to post a
"scan could not complete" comment (step 6) so the developer isn't left hanging,
then exit non-zero so the failure is visible in ECS/CloudWatch.

Env vars (set by the ECS task definition + dispatcher):
  SCAN_JOB           JSON job payload (the SQS message body)
  REPORTS_BUCKET     S3 bucket for reports
  DB_SECRET_ARN      Secrets Manager ARN with RDS credentials (JSON)
  GITHUB_SECRET_ARN  Secrets Manager ARN with the GitHub token (for posting comments)
  AWS_REGION_NAME    AWS region
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scanner")

FAIL_THRESHOLD_SEVERITIES = {"ERROR"}


def _load_job():
    raw = os.environ.get("SCAN_JOB")
    if not raw:
        raise RuntimeError("SCAN_JOB env var is empty; nothing to scan")
    return json.loads(raw)


def _clone_repo(clone_url, head_sha, workdir):
    """Shallow-clone then checkout the exact head SHA so Semgrep sees whole files."""
    log.info("Cloning %s @ %s", clone_url, head_sha[:8])
    subprocess.run(
        ["git", "clone", "--quiet", "--no-checkout", clone_url, workdir],
        check=True,
    )
    subprocess.run(["git", "-C", workdir, "fetch", "--quiet", "--depth", "1", "origin", head_sha], check=True)
    subprocess.run(["git", "-C", workdir, "checkout", "--quiet", head_sha], check=True)


def _run_semgrep(target_dir):
    """Run Semgrep with the auto config; return parsed JSON results."""
    log.info("Running Semgrep on %s", target_dir)
    proc = subprocess.run(
        ["semgrep", "scan", "--config", os.environ.get("SEMGREP_CONFIG", "auto"),
         "--json", "--quiet", "--timeout", "120", target_dir],
        capture_output=True, text=True,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"Semgrep failed (exit {proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout or "{}")


def _summarize(results):
    findings = results.get("results", [])
    by_sev = {}
    for f in findings:
        sev = (f.get("extra", {}) or {}).get("severity", "INFO")
        by_sev[sev] = by_sev.get(sev, 0) + 1
    blocking = sum(by_sev.get(s, 0) for s in FAIL_THRESHOLD_SEVERITIES)
    return {
        "total": len(findings),
        "by_severity": by_sev,
        "blocking": blocking,
        "passed": blocking == 0,
    }


def _upload_report(s3_client, bucket, scan_id, repo_full, pr_number, report):
    key = f"reports/{repo_full}/pr-{pr_number}/{scan_id}.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(report, indent=2).encode(),
        ContentType="application/json",
    )
    url = s3_client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=7 * 24 * 3600
    )
    return key, url


def _html_escape(text):
    """Minimal HTML escaping for untrusted strings (code snippets, messages)."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_html(summary, report, repo_full, pr_number, head_sha):
    """Render a clean, self-contained HTML report (inline CSS, no external assets)."""
    status_label = "PASSED" if summary["passed"] else "FAILED"
    status_color = "#1a7f37" if summary["passed"] else "#cf222e"
    sev_order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    findings = sorted(
        report.get("results", []),
        key=lambda f: sev_order.get((f.get("extra", {}) or {}).get("severity", "INFO"), 3),
    )

    rows = []
    for f in findings:
        extra = f.get("extra", {}) or {}
        sev = extra.get("severity", "INFO")
        sev_bg = {"ERROR": "#ffebe9", "WARNING": "#fff8c5", "INFO": "#ddf4ff"}.get(sev, "#eee")
        path = _html_escape(f.get("path", "unknown"))
        start_line = (f.get("start", {}) or {}).get("line", "?")
        check_id = _html_escape(f.get("check_id", ""))
        message = _html_escape(extra.get("message", ""))
        snippet = _html_escape(extra.get("lines", ""))
        rows.append(f"""
        <div class="finding">
          <div class="sev" style="background:{sev_bg}">{sev}</div>
          <div class="body">
            <div class="loc">{path}:{start_line}</div>
            <div class="msg">{message}</div>
            <pre class="snippet">{snippet}</pre>
            <div class="rule">{check_id}</div>
          </div>
        </div>""")

    findings_html = "".join(rows) if rows else '<p class="none">No findings. 🎉</p>'
    sev_counts = ", ".join(f"{k}: {v}" for k, v in sorted(summary["by_severity"].items())) or "none"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR Scan Report — {_html_escape(repo_full)} #{pr_number}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #f6f8fa; color: #1f2328; }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 24px; }}
  header {{ background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 20px 24px; margin-bottom: 16px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .status {{ display: inline-block; color: #fff; background: {status_color}; padding: 4px 12px; border-radius: 20px; font-weight: 600; font-size: 14px; }}
  .meta {{ color: #656d76; font-size: 14px; margin-top: 10px; }}
  .meta code {{ background: #eaeef2; padding: 1px 6px; border-radius: 4px; }}
  .finding {{ display: flex; background: #fff; border: 1px solid #d0d7de; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }}
  .sev {{ flex: 0 0 90px; font-weight: 700; font-size: 13px; padding: 16px 12px; text-align: center; }}
  .body {{ padding: 14px 16px; flex: 1; min-width: 0; }}
  .loc {{ font-family: ui-monospace, monospace; font-size: 13px; color: #0969da; margin-bottom: 6px; }}
  .msg {{ font-size: 14px; margin-bottom: 8px; }}
  .snippet {{ background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 10px; font-size: 12px; overflow-x: auto; margin: 0 0 6px; }}
  .rule {{ font-size: 12px; color: #656d76; font-family: ui-monospace, monospace; }}
  .none {{ font-size: 16px; color: #1a7f37; }}
  footer {{ color: #656d76; font-size: 12px; text-align: center; margin-top: 20px; }}
</style></head>
<body><div class="wrap">
<header>
  <h1>PR Scan Report</h1>
  <span class="status">{status_label}</span>
  <div class="meta">
    {_html_escape(repo_full)} · PR #{pr_number} · commit <code>{_html_escape(head_sha[:8])}</code><br>
    {summary['total']} finding(s) — {sev_counts} · {summary['blocking']} blocking
  </div>
</header>
{findings_html}
<footer>Generated by PR Scanner · Semgrep SAST</footer>
</div></body></html>"""


def _upload_html(s3_client, bucket, scan_id, repo_full, pr_number, html):
    key = f"reports/{repo_full}/pr-{pr_number}/{scan_id}.html"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=html.encode(),
        ContentType="text/html",
    )
    url = s3_client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=7 * 24 * 3600
    )
    return key, url


def _ensure_schema(db_conn):
    """Create the scans table if it doesn't exist. Idempotent; safe to run every time."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                scan_id           UUID PRIMARY KEY,
                repo              TEXT        NOT NULL,
                pr_number         INTEGER     NOT NULL,
                head_sha          TEXT        NOT NULL,
                status            TEXT        NOT NULL,
                total_findings    INTEGER     NOT NULL DEFAULT 0,
                blocking_findings INTEGER     NOT NULL DEFAULT 0,
                s3_key            TEXT,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_scans_repo_pr ON scans (repo, pr_number);
            CREATE INDEX IF NOT EXISTS idx_scans_created ON scans (created_at DESC);
            """
        )
    db_conn.commit()


def _write_metadata(db_conn, row):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scans
              (scan_id, repo, pr_number, head_sha, status, total_findings, blocking_findings, s3_key, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (scan_id) DO UPDATE SET
              status = EXCLUDED.status,
              total_findings = EXCLUDED.total_findings,
              blocking_findings = EXCLUDED.blocking_findings,
              s3_key = EXCLUDED.s3_key
            """,
            (row["scan_id"], row["repo"], row["pr_number"], row["head_sha"],
             row["status"], row["total"], row["blocking"], row["s3_key"]),
        )
    db_conn.commit()


def _build_comment(summary, presigned_url):
    if summary["passed"]:
        header = "✅ PR Scan passed — no blocking findings"
    else:
        header = f"❌ PR Scan failed — {summary['blocking']} blocking finding(s)"
    sev_lines = ", ".join(f"{k}: {v}" for k, v in sorted(summary["by_severity"].items())) or "none"
    return (
        f"{header}\n\n"
        f"Total findings: {summary['total']} ({sev_lines})\n\n"
        f"[View full report]({presigned_url}) (link valid 7 days)"
    )


def _post_comment(token, comments_url, body):
    import urllib.request
    req = urllib.request.Request(
        comments_url,
        data=json.dumps({"body": body}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "pr-scanner",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def _get_secret_json(secrets_client, arn):
    return json.loads(secrets_client.get_secret_value(SecretId=arn)["SecretString"])


def _get_secret_string(secrets_client, arn):
    return secrets_client.get_secret_value(SecretId=arn)["SecretString"]


def main():
    import boto3
    import psycopg2

    region = os.environ["AWS_REGION_NAME"]
    bucket = os.environ["REPORTS_BUCKET"]
    db_secret_arn = os.environ["DB_SECRET_ARN"]
    gh_secret_arn = os.environ["GITHUB_SECRET_ARN"]

    s3 = boto3.client("s3", region_name=region)
    secrets = boto3.client("secretsmanager", region_name=region)

    job = _load_job()
    repo = job["repo"]
    pr = job["pull_request"]
    scan_id = str(uuid.uuid4())
    comments_url = pr["comments_url"]

    gh_token = _get_secret_string(secrets, gh_secret_arn)

    try:
        with tempfile.TemporaryDirectory() as workdir:
            _clone_repo(repo["clone_url"], pr["head_sha"], workdir)
            results = _run_semgrep(workdir)

        summary = _summarize(results)
        s3_key, presigned = _upload_report(s3, bucket, scan_id, repo["full_name"], pr["number"], results)

        html = _render_html(summary, results, repo["full_name"], pr["number"], pr["head_sha"])
        _, html_url = _upload_html(s3, bucket, scan_id, repo["full_name"], pr["number"], html)

        db = _get_secret_json(secrets, db_secret_arn)
        conn = psycopg2.connect(
            host=db["host"], port=db["port"], dbname=db["dbname"],
            user=db["username"], password=db["password"], connect_timeout=10,
        )
        try:
            _ensure_schema(conn)
            _write_metadata(conn, {
                "scan_id": scan_id, "repo": repo["full_name"], "pr_number": pr["number"],
                "head_sha": pr["head_sha"], "status": "passed" if summary["passed"] else "failed",
                "total": summary["total"], "blocking": summary["blocking"], "s3_key": s3_key,
            })
        finally:
            conn.close()

        body = _build_comment(summary, html_url)
        _post_comment(gh_token, comments_url, body)
        log.info("Scan complete: %s findings, %s blocking", summary["total"], summary["blocking"])
        sys.exit(0)

    except Exception as exc:
        log.exception("Scan failed")
        try:
            _post_comment(gh_token, comments_url,
                          f"⚠️ Scan could not complete: {type(exc).__name__}. See CI logs.")
        except Exception:
            log.error("Also failed to post failure comment")
        sys.exit(1)


if __name__ == "__main__":
    main()
