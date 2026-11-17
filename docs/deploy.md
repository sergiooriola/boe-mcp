# Deploy to AWS

How to put the server on AWS and connect Claude to it. Every AWS resource is in
[`infra/`](../infra); this page covers the steps around it.

The expected cost is a few cents a month (see the [README](../README.md#cost)). The
configuration creates a monthly budget of 2 USD that emails at half of it.

## 1. An AWS account of its own

Use an account that holds nothing else, so its bill is the server's cost and nothing else can
reach it.

1. [Create an AWS account](https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html)
   on the **Paid plan**. The Free plan closes the account after six months.
2. [Turn on MFA for the root user](https://docs.aws.amazon.com/IAM/latest/UserGuide/enable-virt-mfa-for-root.html),
   then stop using it.
3. Enable the **Europe (Spain)** Region (`eu-south-2`), which is opt-in: account menu >
   **Account** > **AWS Regions** > Europe (Spain) > **Enable**
   ([guide](https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-regions.html)).
4. Create an IAM user for yourself with console access and the `AdministratorAccess` policy,
   and register an MFA device for it
   ([guide](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_users_create.html)). It needs no
   access keys: the CLI signs in through the console.

## 2. Sign in from the terminal

```sh
aws configure set region eu-south-2 --profile boe-mcp
aws login --profile boe-mcp
```

[`aws login`](https://docs.aws.amazon.com/cli/latest/reference/login/) opens the browser, takes
the console sign-in and keeps short-lived credentials that it refreshes. The `Makefile` hands
them to Terraform with `aws configure export-credentials`.

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

## 6. Connect Claude

**Claude Code**, with the public client and its fixed callback port:

```sh
claude mcp add --transport http --scope user \
  --client-id "$(terraform -chdir=infra output -raw claude_code_client_id)" \
  --callback-port "$(terraform -chdir=infra output -raw claude_code_callback_port)" \
  boe "$(terraform -chdir=infra output -raw mcp_url)"
```

Then run `/mcp` in Claude Code, choose `boe` and sign in.

**Claude.ai**: Settings > Connectors > **Add custom connector**. Enter the `mcp_url` output,
and under **Advanced settings** choose your own OAuth client with the `claude_ai_client_id`
output and the secret from `terraform -chdir=infra output -raw claude_ai_client_secret`.

## 7. Check it

Ask Claude something only the BOE knows today, for example: «¿Qué disposiciones generales
publica hoy el BOE?». The function's log group, `/aws/lambda/boe-mcp`, shows one line per tool
call with the tool, the outcome and the duration, never the arguments.

## Remove everything

The user pool has deletion protection. Set `deletion_protection = "INACTIVE"` in
`infra/auth.tf`, apply, then:

```sh
creds=$(aws configure export-credentials --profile boe-mcp --format env) && eval "$creds" &&
  terraform -chdir=infra destroy
```

The state bucket stays (`prevent_destroy`); empty and delete it by hand if the account is
closing.
