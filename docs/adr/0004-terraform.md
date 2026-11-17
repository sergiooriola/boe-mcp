---
status: accepted
date: 2026-11-17
---

# Define the infrastructure with Terraform, state in S3

## Context and Problem Statement

The AWS resources (API, function, user pool, clients, budget) must be reproducible, reviewable
in pull requests and destroyable in one step. Where are they defined?

## Considered Options

* The AWS console
* AWS SAM or CloudFormation
* AWS CDK
* Terraform

## Decision Outcome

Chosen option: "Terraform" (1.17, `hashicorp/aws` 6.x), with state in an S3 bucket that uses
S3-native locking (`use_lockfile`, generally available since Terraform 1.11; DynamoDB locking
is deprecated).

Terraform is declarative, works the same across clouds, and every change shows a plan before
it is applied. SAM would be shorter for the function alone, but less so for Cognito. The CDK
adds a program to maintain in a second language. The console leaves no record.

The state bucket is created once by `infra/bootstrap`, with versioning, encryption, public
access blocked and TLS-only access, because the state holds the Claude.ai client secret.

### Consequences

* Good, because CI checks formatting and validates both configurations on every pull request.
* Bad, because the state contains a secret: access to the bucket must stay limited to the
  account's administrators.
