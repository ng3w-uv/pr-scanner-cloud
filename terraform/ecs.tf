resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-cluster" })
}

resource "aws_cloudwatch_log_group" "scanner" {
  name              = "/ecs/${local.name_prefix}-scanner"
  retention_in_days = 7

  tags = local.tags
}

resource "aws_ecs_task_definition" "scanner" {
  family                   = "${local.name_prefix}-scanner"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.scanner_cpu
  memory                   = var.scanner_memory

  execution_role_arn = local.lambda_role_arn
  task_role_arn      = local.lambda_role_arn

  container_definitions = jsonencode([
    {
      name      = "scanner"
      image     = "${aws_ecr_repository.scanner.repository_url}:${var.scanner_image_tag}"
      essential = true

      environment = [
        { name = "REPORTS_BUCKET", value = aws_s3_bucket.reports.id },
        { name = "DB_SECRET_ARN", value = aws_secretsmanager_secret.db.arn },
        { name = "GITHUB_SECRET_ARN", value = aws_secretsmanager_secret.github_token.arn },
        { name = "AWS_REGION_NAME", value = var.aws_region }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.scanner.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "scan"
        }
      }
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-scanner" })
}
