"""
Local tests for the scanner's pure logic — no AWS, Git, Semgrep, or DB needed.

We import scan.py and test the functions that don't touch external systems:
_summarize (counts findings by severity, decides pass/fail) and _build_comment
(formats the PR comment). These are the parts most likely to have bugs and the
easiest to verify deterministically.

Run:  python3 test_scan.py
"""

import sys
import types

fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = lambda *a, **k: None
sys.modules["boto3"] = fake_boto3
sys.modules["psycopg2"] = types.ModuleType("psycopg2")

import scan

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(cond)
    print(f"  [{PASS if cond else FAIL}] {name}")


def _semgrep_output(severities):
    return {"results": [{"extra": {"severity": s}} for s in severities]}


def test_clean_scan_passes():
    print("scan with no findings")
    s = scan._summarize(_semgrep_output([]))
    check("total is 0", s["total"] == 0)
    check("blocking is 0", s["blocking"] == 0)
    check("passed is True", s["passed"] is True)


def test_only_warnings_passes():
    print("scan with WARNING/INFO only (no ERROR)")
    s = scan._summarize(_semgrep_output(["WARNING", "INFO", "WARNING"]))
    check("total is 3", s["total"] == 3)
    check("blocking is 0", s["blocking"] == 0)
    check("passed is True (warnings don't block)", s["passed"] is True)


def test_errors_fail():
    print("scan with ERROR findings")
    s = scan._summarize(_semgrep_output(["ERROR", "WARNING", "ERROR"]))
    check("total is 3", s["total"] == 3)
    check("blocking is 2", s["blocking"] == 2)
    check("passed is False", s["passed"] is False)


def test_severity_breakdown():
    print("severity counts are correct")
    s = scan._summarize(_semgrep_output(["ERROR", "ERROR", "WARNING", "INFO", "INFO", "INFO"]))
    check("2 ERROR", s["by_severity"].get("ERROR") == 2)
    check("1 WARNING", s["by_severity"].get("WARNING") == 1)
    check("3 INFO", s["by_severity"].get("INFO") == 3)


def test_passing_comment():
    print("comment for a passing scan")
    s = scan._summarize(_semgrep_output(["WARNING"]))
    body = scan._build_comment(s, "https://example.com/report")
    check("has pass marker", "passed" in body)
    check("includes report link", "https://example.com/report" in body)
    check("mentions total", "Total findings: 1" in body)


def test_failing_comment():
    print("comment for a failing scan")
    s = scan._summarize(_semgrep_output(["ERROR", "ERROR"]))
    body = scan._build_comment(s, "https://example.com/report")
    check("has fail marker", "failed" in body)
    check("states blocking count", "2 blocking" in body)


def test_html_renders_status_and_findings():
    print("HTML report renders status and findings")
    s = scan._summarize(_semgrep_output(["ERROR", "WARNING"]))
    report = {"results": [
        {"check_id": "rules.sql-injection", "path": "app/db.py",
         "start": {"line": 42},
         "extra": {"severity": "ERROR", "message": "Possible SQL injection",
                   "lines": "query = 'SELECT * FROM u WHERE id=' + uid"}},
        {"check_id": "rules.weak-hash", "path": "app/auth.py",
         "start": {"line": 10},
         "extra": {"severity": "WARNING", "message": "Weak hash", "lines": "md5(pw)"}},
    ]}
    html = scan._render_html(s, report, "octo/demo", 42, "a" * 40)
    check("is an HTML document", html.lstrip().startswith("<!DOCTYPE html>"))
    check("shows FAILED status", "FAILED" in html)
    check("includes the file path", "app/db.py" in html)
    check("includes the line number", "42" in html)
    check("includes the rule id", "sql-injection" in html)
    check("includes repo and PR", "octo/demo" in html and "#42" in html)


def test_html_escapes_malicious_content():
    print("HTML escapes untrusted snippet content")
    s = scan._summarize(_semgrep_output(["ERROR"]))
    report = {"results": [
        {"check_id": "x", "path": "evil.py", "start": {"line": 1},
         "extra": {"severity": "ERROR", "message": "<script>alert(1)</script>",
                   "lines": "<img src=x onerror=alert(1)>"}},
    ]}
    html = scan._render_html(s, report, "octo/demo", 1, "b" * 40)
    check("raw <script> not present", "<script>alert(1)</script>" not in html)
    check("escaped form present", "&lt;script&gt;" in html)


def test_html_clean_scan():
    print("HTML for a clean scan shows the no-findings state")
    s = scan._summarize(_semgrep_output([]))
    html = scan._render_html(s, {"results": []}, "octo/demo", 7, "c" * 40)
    check("shows PASSED", "PASSED" in html)
    check("shows no-findings message", "No findings" in html)


def test_load_job_from_env():
    print("_load_job parses SCAN_JOB env var")
    import os, json
    os.environ["SCAN_JOB"] = json.dumps({"repo": {"full_name": "octo/demo"}})
    job = scan._load_job()
    check("parsed repo name", job["repo"]["full_name"] == "octo/demo")
    del os.environ["SCAN_JOB"]


def test_load_job_empty_raises():
    print("_load_job raises when SCAN_JOB missing")
    import os
    os.environ.pop("SCAN_JOB", None)
    try:
        scan._load_job()
        check("should have raised", False)
    except RuntimeError:
        check("raised RuntimeError as expected", True)


if __name__ == "__main__":
    for t in [
        test_clean_scan_passes,
        test_only_warnings_passes,
        test_errors_fail,
        test_severity_breakdown,
        test_passing_comment,
        test_failing_comment,
        test_html_renders_status_and_findings,
        test_html_escapes_malicious_content,
        test_html_clean_scan,
        test_load_job_from_env,
        test_load_job_empty_raises,
    ]:
        t()
        print()
    total, passed = len(results), sum(results)
    print("=" * 40)
    print(f"{passed}/{total} assertions passed")
    sys.exit(0 if passed == total else 1)
