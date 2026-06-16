data "archive_file" "dispatcher" {
  type        = "zip"
  source_file = "${path.module}/../lambdas/dispatcher/handler.py"
  output_path = "${path.module}/build/dispatcher_lambda.zip"
}

resource "aws_lambda_function" "dispatcher" {
  function_name = "${local.name_prefix}-dispatcher"
  role          = local.lambda_role_arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30

  filename         = data.archive_file.dispatcher.output_path
  source_code_hash = data.archive_file.dispatcher.output_base64sha256

  environment {
    variables = {
      CLUSTER_ARN         = aws_ecs_cluster.main.arn
      TASK_DEFINITION_ARN = aws_ecs_task_definition.scanner.arn
      SUBNET_IDS          = join(",", aws_subnet.private[*].id)
      SECURITY_GROUP_ID   = aws_security_group.fargate.id
      CONTAINER_NAME      = "scanner"
    }
  }

  tags = local.tags
}

resource "aws_lambda_event_source_mapping" "dispatcher" {
  event_source_arn        = aws_sqs_queue.jobs.arn
  function_name           = aws_lambda_function.dispatcher.arn
  batch_size              = 5
  function_response_types = ["ReportBatchItemFailures"]
}
