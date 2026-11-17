# The public HTTPS endpoint (API Gateway HTTP API) and the function behind it (AWS Lambda).
#
# The API is created first and the function reads its URL from the environment. That URL is
# also the OAuth resource the tokens are bound to (auth.tf), so it has to be known before the
# function exists; a Lambda function URL would only be known after.

locals {
  mcp_url        = "${aws_apigatewayv2_api.mcp.api_endpoint}/mcp"
  issuer_url     = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
  required_scope = "${local.mcp_url}/read"
  # Lambda Web Adapter runs the unchanged web app inside Lambda. Published by AWS:
  # https://github.com/awslabs/aws-lambda-web-adapter
  web_adapter_layer = "arn:aws:lambda:${var.region}:753240598075:layer:LambdaAdapterLayerArm64:30"
}

resource "aws_apigatewayv2_api" "mcp" {
  name          = var.name
  protocol_type = "HTTP"
  description   = "MCP server for the BOE"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.mcp.id
  name        = "$default"
  auto_deploy = true

  # A ceiling on what reaches the function: it caps the bill and the load sent to the BOE.
  default_route_settings {
    throttling_rate_limit  = var.throttling_rate_limit
    throttling_burst_limit = var.throttling_burst_limit
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.mcp.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.mcp.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.mcp.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mcp.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mcp.execution_arn}/*/*"
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 30
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

resource "aws_iam_role" "lambda" {
  name               = "${var.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# The function only writes its own logs. It calls the BOE over the internet and needs no other
# AWS permission.
data "aws_iam_policy_document" "lambda_logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda_logs" {
  name   = "write-own-logs"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_logs.json
}

resource "aws_lambda_function" "mcp" {
  function_name    = var.name
  description      = "MCP server for the BOE"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.14"
  architectures    = ["arm64"]
  handler          = "run.sh"
  filename         = var.lambda_zip
  source_code_hash = filebase64sha256(var.lambda_zip)
  memory_size      = 512
  # Below API Gateway's 30-second limit, so a slow BOE ends in an error the client can read.
  timeout = 25
  layers  = [local.web_adapter_layer]

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda.name
  }

  environment {
    variables = {
      AWS_LAMBDA_EXEC_WRAPPER      = "/opt/bootstrap"
      AWS_LWA_PORT                 = "8080"
      AWS_LWA_READINESS_CHECK_PATH = "/healthz"
      BOE_MCP_RESOURCE_URL         = local.mcp_url
      BOE_MCP_ISSUER_URL           = local.issuer_url
      BOE_MCP_CLIENT_IDS           = join(",", [aws_cognito_user_pool_client.claude_code.id, aws_cognito_user_pool_client.claude_ai.id])
      BOE_MCP_REQUIRED_SCOPE       = local.required_scope
    }
  }

  depends_on = [aws_iam_role_policy.lambda_logs]
}
