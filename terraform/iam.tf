locals {
  create_role = var.existing_lambda_role_name == ""
}

data "aws_iam_role" "existing" {
  count = local.create_role ? 0 : 1
  name  = var.existing_lambda_role_name
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "webhook" {
  count              = local.create_role ? 1 : 0
  name               = "${local.name_prefix}-webhook-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "webhook_basic" {
  count      = local.create_role ? 1 : 0
  role       = aws_iam_role.webhook[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "webhook" {
  count = local.create_role ? 1 : 0

  statement {
    sid       = "EnqueueScanJobs"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]
  }

  statement {
    sid       = "ReadWebhookSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.webhook.arn]
  }
}

resource "aws_iam_role_policy" "webhook" {
  count  = local.create_role ? 1 : 0
  name   = "${local.name_prefix}-webhook-policy"
  role   = aws_iam_role.webhook[0].id
  policy = data.aws_iam_policy_document.webhook[0].json
}

locals {
  lambda_role_arn = local.create_role ? aws_iam_role.webhook[0].arn : data.aws_iam_role.existing[0].arn
}
