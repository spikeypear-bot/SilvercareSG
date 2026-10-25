terraform {
  # Matches TERRAFORM_VERSION in .github/workflows/*.yml and docker/dev.Dockerfile.
  required_version = "~> 1.16"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # Zips Lambda source folders (lambdas/<name>/src/).
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}
