# Amazon Cognito is the OAuth 2.1 authorization server: it signs users in and issues the access
# tokens that the MCP server checks. The MCP server never sees a password.

resource "aws_cognito_user_pool" "users" {
  name = var.name
  # Essentials is the plan with managed login and resource binding (RFC 8707). Its first
  # 10,000 monthly active users are free.
  user_pool_tier      = "ESSENTIALS"
  deletion_protection = "ACTIVE"

  # Nobody can sign up: an administrator creates each user.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Every user signs in with a password and an authenticator app.
  mfa_configuration = "ON"
  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "random_id" "domain_suffix" {
  byte_length = 4
}

# The managed login pages and the OAuth endpoints live on this domain.
resource "aws_cognito_user_pool_domain" "login" {
  domain                = "${var.name}-${random_id.domain_suffix.hex}"
  user_pool_id          = aws_cognito_user_pool.users.id
  managed_login_version = 2
}

# The MCP server as an OAuth resource. Its identifier is the server's URL, so a client that asks
# for that resource (RFC 8707) gets a token whose audience is this server and nothing else.
resource "aws_cognito_resource_server" "mcp" {
  user_pool_id = aws_cognito_user_pool.users.id
  identifier   = local.mcp_url
  name         = "BOE MCP server"

  scope {
    scope_name        = "read"
    scope_description = "Read the BOE through the MCP server"
  }
}

locals {
  client_defaults = {
    allowed_oauth_flows  = ["code"]
    allowed_oauth_scopes = aws_cognito_resource_server.mcp.scope_identifiers
    # Only refresh through the API: every sign-in goes through managed login.
    explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]
  }
}

# Claude Code runs on the user's computer, cannot keep a secret and receives the code on
# localhost: a public client, protected by PKCE.
resource "aws_cognito_user_pool_client" "claude_code" {
  name                                 = "claude-code"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = false
  callback_urls                        = ["http://localhost:${var.claude_code_callback_port}/callback"]
  supported_identity_providers         = ["COGNITO"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = local.client_defaults.allowed_oauth_flows
  allowed_oauth_scopes                 = local.client_defaults.allowed_oauth_scopes
  explicit_auth_flows                  = local.client_defaults.explicit_auth_flows
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  # A public client's refresh token is replaced on every use, so a stolen one stops working.
  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 10
  }
}

# Claude.ai connects from Anthropic's servers, which can keep a secret: a confidential client.
resource "aws_cognito_user_pool_client" "claude_ai" {
  name                                 = "claude-ai"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = true
  callback_urls                        = ["https://claude.ai/api/mcp/auth_callback"]
  supported_identity_providers         = ["COGNITO"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = local.client_defaults.allowed_oauth_flows
  allowed_oauth_scopes                 = local.client_defaults.allowed_oauth_scopes
  explicit_auth_flows                  = local.client_defaults.explicit_auth_flows
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# Managed login needs a style for each app client; these are Cognito's defaults.
resource "aws_cognito_managed_login_branding" "clients" {
  for_each = {
    claude_code = aws_cognito_user_pool_client.claude_code.id
    claude_ai   = aws_cognito_user_pool_client.claude_ai.id
  }

  user_pool_id                = aws_cognito_user_pool.users.id
  client_id                   = each.value
  use_cognito_provided_values = true
}
