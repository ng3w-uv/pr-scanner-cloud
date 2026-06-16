output "webhook_url" {
  description = "Paste this as the Payload URL in the GitHub webhook settings."
  value       = aws_lambda_function_url.webhook.function_url
}

output "webhook_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the webhook HMAC secret."
  value       = aws_secretsmanager_secret.webhook.arn
}

output "get_webhook_secret_command" {
  description = "Run this to retrieve the generated secret to paste into GitHub's Secret field."
  value       = "aws secretsmanager get-secret-value --secret-id ${aws_secretsmanager_secret.webhook.id} --query SecretString --output text --region ${var.aws_region}"
}

output "jobs_queue_url" {
  description = "URL of the SQS job queue the dispatcher will consume."
  value       = aws_sqs_queue.jobs.id
}

output "jobs_dlq_url" {
  description = "URL of the dead-letter queue."
  value       = aws_sqs_queue.dlq.id
}

output "vpc_id" {
  description = "VPC ID for the dispatcher/Fargate stacks."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnet IDs where Fargate tasks and RDS run."
  value       = aws_subnet.private[*].id
}

output "fargate_security_group_id" {
  description = "Security group to attach to ECS Fargate scan tasks."
  value       = aws_security_group.fargate.id
}

output "db_endpoint" {
  description = "RDS PostgreSQL endpoint (host:port)."
  value       = aws_db_instance.main.endpoint
}

output "db_credentials_secret_arn" {
  description = "Secrets Manager ARN holding RDS connection credentials (JSON)."
  value       = aws_secretsmanager_secret.db.arn
}

output "ecr_repository_url" {
  description = "Push the scanner image here (docker push <url>:latest)."
  value       = aws_ecr_repository.scanner.repository_url
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN the dispatcher will run tasks on."
  value       = aws_ecs_cluster.main.arn
}

output "scanner_task_definition_arn" {
  description = "Task definition ARN (family:revision) the dispatcher passes to RunTask."
  value       = aws_ecs_task_definition.scanner.arn
}

output "reports_bucket" {
  description = "S3 bucket name where scan reports are stored."
  value       = aws_s3_bucket.reports.id
}

output "ecr_login_command" {
  description = "Run this to authenticate Docker to ECR before pushing."
  value       = "aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
}
