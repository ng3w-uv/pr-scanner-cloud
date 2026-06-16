# PR Scanner

A SAST (static application security testing) service that automatically scans GitHub
pull requests for security vulnerabilities and posts a pass/fail result back on the PR.

Built on AWS, deployed with Terraform. When a developer opens a pull request, the
service validates the webhook, runs [Semgrep](https://semgrep.dev) against the code in
an isolated container, stores the report, and comments the result on the PR with a
link to a readable HTML findings report.

**Course:** CS6620 Cloud Computing · **Team:** Aryan Sarode, Yuvraj Chauhan

---

## How it works

```
GitHub PR
   │  webhook (HMAC-signed)
   ▼
Webhook Lambda (Function URL)  ── validates HMAC against Secrets Manager
   │  enqueue job
   ▼
SQS queue ──(retries 3×)──► Dead-letter queue ──► CloudWatch alarm ──► SNS email
   │
   ▼
Dispatcher Lambda  ── polls SQS, launches the scanner
   │  RunTask
   ▼
ECS Fargate (private subnet)  ── clones PR, runs Semgrep
   │
   ├──► S3        (JSON + rendered HTML report, pre-signed link)
   ├──► RDS       (scan metadata, private subnet)
   └──► GitHub PR (pass/fail comment)
```

The scanner and database run in private subnets inside a VPC; outbound access is via a
single NAT gateway. Only `ERROR`-severity Semgrep findings fail a PR — warnings and
info are reported but non-blocking.

---

## Repository structure

```
pr-scanner/
├── lambdas/
│   ├── webhook/         # Webhook Lambda: HMAC validation + enqueue to SQS
│   │   ├── handler.py
│   │   └── test_handler.py
│   └── dispatcher/      # Dispatcher Lambda: polls SQS, launches Fargate task
│       ├── handler.py
│       └── test_handler.py
├── scanner/            # The Semgrep container
│   ├── scan.py         # Clone, scan, upload report, write metadata, post comment
│   ├── test_scan.py
│   ├── Dockerfile
│   └── schema.sql      # RDS metadata schema (also auto-created on first run)
└── terraform/          # All infrastructure as code
    ├── main.tf         # Provider, variables, naming
    ├── vpc.tf          # VPC, subnets, NAT, route tables
    ├── sqs.tf          # Job queue + dead-letter queue
    ├── rds.tf          # RDS PostgreSQL + security groups
    ├── ecr.tf          # Container registry
    ├── ecs.tf          # Cluster + Fargate task definition
    ├── lambda.tf       # Webhook Lambda + Function URL
    ├── dispatcher.tf   # Dispatcher Lambda + SQS event source mapping
    ├── secrets.tf      # Webhook HMAC secret
    ├── github_token.tf # GitHub API token secret
    ├── alerts.tf       # SNS topic + CloudWatch DLQ alarm
    └── outputs.tf
```

---

## Tech stack

| Area | Service |
|------|---------|
| Ingress | Lambda Function URL, Secrets Manager |
| Queue | SQS + dead-letter queue |
| Compute | ECS Fargate, ECR |
| Scanning | Semgrep (`--config auto`) |
| Storage | S3 (reports), RDS PostgreSQL (metadata) |
| Monitoring | CloudWatch, SNS |
| Networking | VPC, public/private subnets, NAT gateway |
| IaC | Terraform |

---

## Running the tests

The unit tests run locally with no AWS account (AWS calls are mocked):

```bash
python3 lambdas/webhook/test_handler.py
python3 lambdas/dispatcher/test_handler.py
python3 scanner/test_scan.py
```

---

## Deploying

> Built and tested in an AWS Academy Learner Lab, which cannot create IAM roles, so the
> stack reuses the pre-existing `LabRole` (see `terraform.tfvars`).

**Prerequisites:** Terraform ≥ 1.5, AWS CLI configured, Docker.

1. **Configure variables** — create `terraform/secret.auto.tfvars`:
   ```
   github_token = "github_pat_..."   # fine-grained PAT, Pull requests: read & write
   alert_email  = "you@example.com"  # for DLQ failure alerts
   ```
   `terraform.tfvars` already sets `existing_lambda_role_name = "LabRole"` and the region.

2. **Deploy the infrastructure:**
   ```bash
   cd terraform
   terraform init
   terraform apply
   ```

3. **Build and push the scanner image** (use the `ecr_*` outputs):
   ```bash
   cd ../scanner
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
   docker build --platform linux/amd64 -t pr-scanner-dev-scanner .
   docker tag pr-scanner-dev-scanner:latest <ecr_repository_url>:latest
   docker push <ecr_repository_url>:latest
   ```

4. **Connect GitHub** — in your repo, add a webhook:
   - Payload URL: `terraform output webhook_url`
   - Content type: `application/json`
   - Secret: value from `terraform output get_webhook_secret_command`
   - Events: Pull requests only

5. **Confirm the SNS subscription** — click the link in the confirmation email.

Open a pull request and the scanner runs automatically.

**Tear down** (the NAT gateway and RDS bill hourly):
```bash
terraform destroy
```

---

## Security notes

- Webhook payloads are verified with HMAC-SHA256 (constant-time compare) before processing.
- The scanner and RDS have no inbound internet access; RDS accepts port 5432 only from the scanner's security group.
- The reports bucket is fully private; reports are shared via time-limited pre-signed URLs.
- The HMAC webhook secret and the GitHub API token are kept as separate secrets.

---

## Future work

- Post a GitHub Check Run so a failing scan can block the merge (not just comment).
- Reduce Fargate cold-start latency with a warm pool or pre-pulled image.
- Split the shared `LabRole` into least-privilege roles per component.
- Add dependency/CVE scanning beyond Semgrep's code-pattern rules.
