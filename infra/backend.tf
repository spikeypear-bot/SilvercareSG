# Remote state in the bucket created by bootstrap/ (literal values: backend blocks
# can't use variables).
terraform {
  backend "s3" {
    bucket       = "silvercare-tfstate-753674274065"
    key          = "silvercare/terraform.tfstate"
    region       = "ap-southeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
