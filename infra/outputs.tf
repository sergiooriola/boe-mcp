output "mcp_url" {
  description = "URL to add to Claude."
  value       = local.mcp_url
}

output "issuer_url" {
  description = "OAuth authorization server (the Cognito user pool)."
  value       = local.issuer_url
}

output "user_pool_id" {
  description = "Cognito user pool, to create users in."
  value       = aws_cognito_user_pool.users.id
}

output "login_domain" {
  description = "Managed login domain."
  value       = "https://${aws_cognito_user_pool_domain.login.domain}.auth.${var.region}.amazoncognito.com"
}

output "claude_code_client_id" {
  description = "OAuth client id for Claude Code."
  value       = aws_cognito_user_pool_client.claude_code.id
}

output "claude_code_callback_port" {
  description = "Callback port to give Claude Code."
  value       = var.claude_code_callback_port
}

output "claude_ai_client_id" {
  description = "OAuth client id for the Claude.ai custom connector."
  value       = aws_cognito_user_pool_client.claude_ai.id
}

output "claude_ai_client_secret" {
  description = "OAuth client secret for the Claude.ai custom connector."
  value       = aws_cognito_user_pool_client.claude_ai.client_secret
  sensitive   = true
}
