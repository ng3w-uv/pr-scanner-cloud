data "archive_file" "webhook" {
  type        = "zip"
  source_file = "${path.module}/../lambdas/webhook/handler.py"
  output_path = "${path.module}/build/webhook_lambda.zip"
}

resource "aws_lambda_function" "webhook" {
  function_name = "${local.name_prefix}-webhook"
  role          = local.lambda_role_arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = var.lambda_timeout_seconds

  filename         = data.archive_file.webhook.output_path
  source_code_hash = data.archive_file.webhook.output_base64sha256

  environment {
    variables = {
      JOB_QUEUE_URL       = aws_sqs_queue.jobs.id
      WEBHOOK_SECRET_ARN  = aws_secretsmanager_secret.webhook.arn
    }
  }

  tags = local.tags
}

resource "aws_lambda_function_url" "webhook" {
  function_name      = aws_lambda_function.webhook.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "webhook_url_invoke" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.webhook.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# NOTE: Since October 2025, public Function URLs also require a lambda:InvokeFunction
# grant scoped to function-URL calls. The Terraform argument that expresses this
# (invoked_via_function_url) requires AWS provider >= 5.40. If your provider is older,
# this permission is added once via the CLI instead (it persists in AWS independently):
#
#   aws lambda add-permission \
#     --function-name pr-scanner-dev-webhook \
#     --statement-id AllowPublicFunctionUrlInvokeFunction \
#     --action lambda:InvokeFunction \
#     --principal "*" \
#     --invoked-via-function-url \
#     --region us-east-1
#
# On provider >= 5.40, uncomment the resource below and remove the CLI step:
#
# resource "aws_lambda_permission" "webhook_url_invoke_function" {
#   statement_id             = "AllowPublicFunctionUrlInvokeFunction"
#   action                   = "lambda:InvokeFunction"
#   function_name            = aws_lambda_function.webhook.function_name
#   principal                = "*"
#   invoked_via_function_url = true
# }
