output "tfstate_bucket_name" {
  description = "Name of the Terraform state bucket (used in ../backend.tf)"
  value       = aws_s3_bucket.tfstate.bucket
}

output "tfstate_bucket_arn" {
  description = "ARN of the Terraform state bucket (for the CI role policies)"
  value       = aws_s3_bucket.tfstate.arn
}

output "ci_plan_role_arn" {
  description = "ARN of the read-only plan role assumed by .github/workflows/plan.yml"
  value       = aws_iam_role.ci_plan.arn
}
