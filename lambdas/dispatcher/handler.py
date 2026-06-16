"""
PR Scanner — dispatcher Lambda.

Triggered by the SQS job queue (event source mapping). For each job message it
launches the scanner as a Fargate task, passing the job payload to the container
via the SCAN_JOB environment override.

Design:
  - boto3 only, runs OUTSIDE the VPC. It never touches RDS; the scanner container
    (inside the VPC) owns all database work, including schema creation.
  - On any failure it raises, so the Lambda reports the message as failed and SQS
    redelivers it. After maxReceiveCount attempts the message lands in the DLQ.
  - Uses ReportBatchItemFailures so one bad message in a batch doesn't force the
    whole batch to retry.

Env vars (from the task definition / Terraform):
  CLUSTER_ARN          ECS cluster to run on
  TASK_DEFINITION_ARN  scanner task definition (family:revision)
  SUBNET_IDS           comma-separated private subnet IDs
  SECURITY_GROUP_ID    Fargate security group
  CONTAINER_NAME       container name in the task def (default "scanner")
"""

import json
import logging
import os

import boto3

logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.INFO)

CLUSTER_ARN = os.environ["CLUSTER_ARN"]
TASK_DEFINITION_ARN = os.environ["TASK_DEFINITION_ARN"]
SUBNET_IDS = [s for s in os.environ["SUBNET_IDS"].split(",") if s]
SECURITY_GROUP_ID = os.environ["SECURITY_GROUP_ID"]
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "scanner")

_ecs = boto3.client("ecs")


def _launch_scan(job: dict):
    """Start one Fargate task, passing the job to the container as SCAN_JOB."""
    resp = _ecs.run_task(
        cluster=CLUSTER_ARN,
        taskDefinition=TASK_DEFINITION_ARN,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNET_IDS,
                "securityGroups": [SECURITY_GROUP_ID],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": CONTAINER_NAME,
                    "environment": [
                        {"name": "SCAN_JOB", "value": json.dumps(job)},
                    ],
                }
            ]
        },
    )

    failures = resp.get("failures", [])
    if failures:
        raise RuntimeError(f"RunTask reported failures: {failures}")

    task_arn = resp["tasks"][0]["taskArn"]
    log.info(
        "Launched scan task %s for repo=%s pr=#%s",
        task_arn.split("/")[-1],
        job["repo"]["full_name"],
        job["pull_request"]["number"],
    )
    return task_arn


def handler(event, context):
    """SQS batch handler. Returns batchItemFailures for any messages that failed."""
    failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            job = json.loads(record["body"])
            _launch_scan(job)
        except Exception:
            log.exception("Failed to dispatch message %s", message_id)
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
