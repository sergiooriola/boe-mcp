# Deploy to AWS

How to put the server on AWS and connect Claude to it. Every AWS resource is in
[`infra/`](../infra); this page covers the steps around it.

The expected cost is a few cents a month (see the [README](../README.md#cost)). The
configuration creates a monthly budget of 2 USD that emails at half of it.

## 1. An AWS account of its own

Use an account that holds nothing else, so its bill is the server's cost and nothing else can
reach it. The tested path is a member account of an organization in
[AWS Organizations](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_introduction.html),
reached through IAM Identity Center. AWS recommends one account per workload this way.

The organization needs three things first:

- IAM Identity Center, with a group that can get the `AdministratorAccess` permission set.
- Trusted access for AWS Account Management, so the management account can enable Regions in
  member accounts.
- [Centralized root access](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_root-enable-root-access.html),
  so new member accounts have no root password.

The new account also needs an email address that no other account uses. A group address read
by more than one person is best.

From the management account (here, the `management` CLI profile):

```sh
aws organizations create-account --profile management \
  --email aws-boe-mcp@example.com --account-name example-boe-mcp-prod
aws organizations describe-create-account-status --profile management \
  --create-account-request-id <request id from the previous command>
```

When the status is `SUCCEEDED`, it gives the account id. Then enable **Europe (Spain)**
(`eu-south-2`), which is opt-in. Wait until the second command says `ENABLED`, which takes a few
minutes:

```sh
aws account enable-region --profile management \
  --account-id <account id> --region-name eu-south-2
aws account get-region-opt-status --profile management \
  --account-id <account id> --region-name eu-south-2
```

Then give your Identity Center group access to the account. `aws sso-admin list-instances`,
`aws sso-admin list-permission-sets` and `aws identitystore list-groups` give the ARNs and the
group id:

```sh
aws sso-admin create-account-assignment --profile management \
  --instance-arn <instance ARN> --target-type AWS_ACCOUNT --target-id <account id> \
  --permission-set-arn <AdministratorAccess permission set ARN> \
  --principal-type GROUP --principal-id <group id>
```

## 2. Sign in from the terminal

Add a profile for the new account to `~/.aws/config`, next to the `sso-session` you already
use:

```ini
[profile boe-mcp]
sso_session = <your sso-session name>
sso_account_id = <account id>
sso_role_name = AdministratorAccess
region = eu-south-2
```

```sh
aws sso login --sso-session <your sso-session name>
aws sts get-caller-identity --profile boe-mcp
```

The second command should show the new account. The first minute after the assignment it can
answer `ForbiddenException: No access`, while Identity Center creates the role.

Add `--use-device-code` to `aws sso login` where the CLI cannot open a browser, as in WSL. It
prints a URL and a code to type in any browser.

The `Makefile` hands the short-lived credentials to Terraform with
`aws configure export-credentials`.

## 3. State bucket, once

Terraform takes the credentials from the shell. Each block chains its commands with `&&`, so if
the `boe-mcp` session has expired nothing runs with whatever other credentials the shell has.

```sh
creds=$(aws configure export-credentials --profile boe-mcp --format env) && eval "$creds" &&
  terraform -chdir=infra/bootstrap init &&
  terraform -chdir=infra/bootstrap apply
```

Copy `infra/backend.hcl.example` to `infra/backend.hcl` with the bucket name it prints, and
`infra/terraform.tfvars.example` to `infra/terraform.tfvars` with the address for budget
alerts. Git ignores both files. Then:

```sh
creds=$(aws configure export-credentials --profile boe-mcp --format env) && eval "$creds" &&
  terraform -chdir=infra init -backend-config=backend.hcl
```

## 4. Deploy

```sh
make deploy
```

It builds `build/lambda.zip` for arm64, shows the plan and applies it after you confirm.
Later deploys are the same command; Terraform only changes what changed.

## 5. Create your user

Nobody can sign up: an administrator creates each user. Cognito emails a temporary password,
and the first sign-in asks for a new password and for an authenticator app.

```sh
aws cognito-idp admin-create-user --profile boe-mcp \
  --user-pool-id "$(terraform -chdir=infra output -raw user_pool_id)" \
  --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL
```

The email comes from `no-reply@verificationemail.com` and may land in spam. The temporary
password expires after three days.

## 6. Connect Claude

**Claude Code**, with the public client and its fixed callback port:

```sh
claude mcp add --transport http --scope user \
  --client-id "$(terraform -chdir=infra output -raw claude_code_client_id)" \
  --callback-port "$(terraform -chdir=infra output -raw claude_code_callback_port)" \
  boe "$(terraform -chdir=infra output -raw mcp_url)"
```

Claude Code reads its MCP servers when a session starts, so open a new session, run `/mcp`,
choose `boe` and sign in. Where it cannot open a browser, as in WSL, it shows the sign-in URL to
open by hand.

**Claude.ai**: Settings > Connectors > **Add custom connector**. Enter the `mcp_url` output,
and under **Advanced settings** choose your own OAuth client with the `claude_ai_client_id`
output and the secret from `terraform -chdir=infra output -raw claude_ai_client_secret`.

## 7. Check it

Ask Claude something only the BOE knows today, for example: «¿Qué disposiciones generales
publica hoy el BOE?». The function's log group, `/aws/lambda/boe-mcp`, shows one line per tool
call with the tool, the outcome and the duration, never the arguments. Each request also leaves
an access line with the caller's IP address, the path and the status, which helps to trace
abuse and failures. Like the rest of the log, it is deleted after 30 days.

## Remove everything

The user pool has deletion protection. Set `deletion_protection = "INACTIVE"` in
`infra/auth.tf`, apply, then:

```sh
creds=$(aws configure export-credentials --profile boe-mcp --format env) && eval "$creds" &&
  terraform -chdir=infra destroy
```

The state bucket stays (`prevent_destroy`); empty and delete it by hand if the account is
closing.
