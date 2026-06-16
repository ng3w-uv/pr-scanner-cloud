resource "aws_secretsmanager_secret" "webhook" {
  name        = "${local.name_prefix}-github-webhook-secret"
  description = "Shared secret GitHub uses to HMAC-sign webhook payloads."

  recovery_window_in_days = 0

  tags = local.tags
}

resource "random_password" "webhook" {
  length  = 40
  special = false
}

resource "aws_secretsmanager_secret_version" "webhook" {
  secret_id     = aws_secretsmanager_secret.webhook.id
  secret_string = random_password.webhook.result
}
