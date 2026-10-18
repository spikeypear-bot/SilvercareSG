output "registry_table_name" {
  description = "Name of the silvercare-registry table."
  value       = module.registry_table.name
}

output "registry_table_arn" {
  description = "ARN of the silvercare-registry table."
  value       = module.registry_table.arn
}

output "latest_table_name" {
  description = "Name of the silvercare-latest table."
  value       = module.latest_table.name
}

output "latest_table_arn" {
  description = "ARN of the silvercare-latest table."
  value       = module.latest_table.arn
}

output "alerts_table_name" {
  description = "Name of the silvercare-alerts table."
  value       = module.alerts_table.name
}

output "alerts_table_arn" {
  description = "ARN of the silvercare-alerts table."
  value       = module.alerts_table.arn
}

output "frontend_bucket_name" {
  description = "Name of the S3 bucket holding the frontend build."
  value       = aws_s3_bucket.frontend.bucket
}

output "frontend_distribution_id" {
  description = "ID of the frontend CloudFront distribution (for cache invalidations)."
  value       = aws_cloudfront_distribution.frontend.id
}

output "frontend_distribution_domain" {
  description = "Default *.cloudfront.net domain of the frontend distribution."
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "frontend_url" {
  description = "HTTPS URL of the frontend."
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "cognito_user_pool_id" {
  description = "ID of the silvercare-users Cognito user pool (frontend VITE_USER_POOL_ID)."
  value       = aws_cognito_user_pool.users.id
}

output "cognito_user_pool_client_id" {
  description = "ID of the silvercare-web app client (frontend VITE_USER_POOL_CLIENT_ID)."
  value       = aws_cognito_user_pool_client.web.id
}

output "cognito_user_pool_arn" {
  description = "ARN of the silvercare-users Cognito user pool."
  value       = aws_cognito_user_pool.users.arn
}
