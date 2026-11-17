variable "region" {
  description = "AWS Region for every resource."
  type        = string
  default     = "eu-south-2"
}

variable "name" {
  description = "Name of the service, used to name its resources."
  type        = string
  default     = "boe-mcp"
}

variable "lambda_zip" {
  description = "Deployment package built by scripts/build-lambda.sh."
  type        = string
  default     = "../build/lambda.zip"
}

variable "claude_code_callback_port" {
  description = "Fixed localhost port of Claude Code's OAuth callback. Cognito matches callback URLs exactly, so the port cannot change at random."
  type        = number
  default     = 33418
}

variable "throttling_rate_limit" {
  description = "Steady requests per second that API Gateway lets through to the function."
  type        = number
  default     = 5
}

variable "throttling_burst_limit" {
  description = "Requests that API Gateway lets through at once before throttling."
  type        = number
  default     = 10
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget for the account; alerts are sent above half of it."
  type        = number
  default     = 2
}

variable "budget_email" {
  description = "Address that receives the budget alerts. Set it in terraform.tfvars, which git ignores."
  type        = string
  sensitive   = true
}
