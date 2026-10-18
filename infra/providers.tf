provider "aws" {
  region = "ap-southeast-1"

  default_tags {
    tags = {
      project    = "silvercare"
      managed_by = "terraform"
    }
  }
}
