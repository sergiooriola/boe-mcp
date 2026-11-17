terraform {
  # 1.11 makes S3-native state locking (use_lockfile) generally available.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # Bucket, key and region come from backend.hcl (see backend.hcl.example).
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project    = var.name
      ManagedBy  = "terraform"
      Repository = "https://github.com/sergiooriola/boe-mcp"
    }
  }
}
