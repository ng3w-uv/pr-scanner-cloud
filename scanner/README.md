# PR Scanner — scanner container

The Semgrep container that runs inside Fargate. Clones the PR, runs Semgrep,
uploads the report to S3, writes metadata to RDS, posts the pass/fail comment.

## Files

- `scan.py` — the scan logic (clone, Semgrep, S3, RDS, comment).
- `test_scan.py` — local logic tests (no AWS/Docker needed): `python3 test_scan.py`.
- `Dockerfile` — Semgrep base image + Git + psycopg2 + boto3 + scan.py.
- `schema.sql` — the `scans` metadata table to create in RDS.

## Build and push to ECR

From this `scanner/` folder, with lab credentials active. Use the exact values
your `terraform output` printed.

```bash
# 1. Authenticate Docker to ECR (this is the ecr_login_command output)
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 950168522979.dkr.ecr.us-east-1.amazonaws.com

# 2. Build (linux/amd64 is required — Fargate runs amd64)
docker build --platform linux/amd64 -t pr-scanner-dev-scanner .

# 3. Tag for ECR (use your ecr_repository_url output)
docker tag pr-scanner-dev-scanner:latest \
  950168522979.dkr.ecr.us-east-1.amazonaws.com/pr-scanner-dev-scanner:latest

# 4. Push
docker push 950168522979.dkr.ecr.us-east-1.amazonaws.com/pr-scanner-dev-scanner:latest
```

If you're on an Apple Silicon Mac, the `--platform linux/amd64` flag in step 2 is
essential — without it the image is arm64 and Fargate will fail to start the task.

## Create the database schema (one time)

The `scans` table must exist before the scanner writes to it. RDS is in a private
subnet, so you can't reach it directly from your laptop. Easiest path: run the
schema from inside the VPC once. Options, simplest first:

1. Temporarily flip the RDS instance to publicly accessible + open the security
   group to your IP, run `psql -f schema.sql`, then revert. Quick but manual.
2. Run a one-off Fargate task (or EC2 in the VPC) that applies the schema.

We'll wire schema creation into the dispatcher's first run instead, so this is
usually unnecessary — see the dispatcher step.

## Test locally first

```bash
python3 test_scan.py   # 19 assertions, no Docker required
```
