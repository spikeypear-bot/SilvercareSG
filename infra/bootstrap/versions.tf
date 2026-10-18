# Applied once by hand, never by CI. No backend block: bootstrap uses local state.
terraform {
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-southeast-1"

  # Refuse to run with credentials for any other account.
  allowed_account_ids = ["753674274065"]

  default_tags {
    tags = {
      project    = "silvercare"
      managed_by = "terraform"
    }
  }
}
