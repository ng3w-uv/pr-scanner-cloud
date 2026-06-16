resource "aws_secretsmanager_secret" "github_token" {
  name                    = "${local.name_prefix}-github-token"
  description             = "GitHub fine-grained PAT used by the scanner to post PR comments."
  recovery_window_in_days = 0

  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "github_token" {
  secret_id     = aws_secretsmanager_secret.github_token.id
  secret_string = var.github_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}
