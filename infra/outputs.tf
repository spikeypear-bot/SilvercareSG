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

output "iot_endpoint" {
  description = "AWS IoT Core ATS data endpoint (host name) that devices connect to over MQTT/TLS on port 8883. Sensitive so the deploy job's apply step doesn't print it in the public Actions log; read it with `terraform output -raw iot_endpoint`."
  value       = data.aws_iot_endpoint.data.endpoint_address
  sensitive   = true
}

output "device_certificates" {
  description = "Map of device ID to certificate PEM. Certificates are public, but this is sensitive so the deploy job's apply step doesn't print it in the public Actions log; save it with `terraform output -json device_certificates` for make device-certs."
  value       = module.iot_devices.device_certificates
  sensitive   = true
}
