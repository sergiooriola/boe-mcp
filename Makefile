# Common tasks. Deployment needs an AWS CLI profile: AWS_PROFILE=boe-mcp make deploy
AWS_PROFILE ?= boe-mcp
# Terraform gets short-lived credentials from the AWS CLI, whatever way the profile signs in. If
# the CLI fails (an expired session), the recipe stops instead of running with other credentials.
AWS_CREDENTIALS = creds="$$(aws configure export-credentials --profile $(AWS_PROFILE) --format env)" && eval "$$creds"

.PHONY: check build plan deploy

check: ## Format, lint, type-check and test, as CI does
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest

build: ## Build the Lambda package for arm64
	./scripts/build-lambda.sh

plan: build ## Show what a deploy would change
	$(AWS_CREDENTIALS) && terraform -chdir=infra plan

deploy: build ## Build and deploy to AWS
	$(AWS_CREDENTIALS) && terraform -chdir=infra apply
