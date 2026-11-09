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


output "history_queue_url" {
  description = "URL of the silvercare-history SQS queue (second action of every ingest IoT rule)."
  value       = aws_sqs_queue.history.url
}

output "history_dlq_url" {
  description = "URL of the silvercare-history-dlq SQS queue (history messages that failed 5 receives)."
  value       = aws_sqs_queue.history_dlq.url
}

output "history_bucket_name" {
  description = "Name of the S3 bucket holding the telemetry history (gzipped JSON Lines under telemetry/)."
  value       = aws_s3_bucket.history.bucket
}

output "alerts_queue_url" {
  description = "URL of the silvercare-alerts SQS queue (critical-alert and telemetry send alert messages here; alert-processor reads it)."
  value       = aws_sqs_queue.alerts.url
}

output "alerts_dlq_url" {
  description = "URL of the silvercare-alerts-dlq SQS queue (alert messages that failed 5 receives)."
  value       = aws_sqs_queue.alerts_dlq.url
}

output "critical_alert_dlq_url" {
  description = "URL of the silvercare-critical-alert-dlq SQS queue (failed critical-alert invocations and silvercare_alert rule errors)."
  value       = aws_sqs_queue.critical_alert_dlq.url
}

output "alerts_topic_arn" {
  description = "ARN of the silvercare-alerts SNS topic (alert emails, filtered by resident_id)."
  value       = aws_sns_topic.alerts.arn
}

output "ops_topic_arn" {
  description = "ARN of the silvercare-ops SNS topic (DLQ alarm emails)."
  value       = aws_sns_topic.ops.arn
}

output "api_url" {
  description = "Base URL of the silvercare-api HTTP API ($default stage), for the frontend's VITE_API_URL. Not sensitive: it ships in the frontend bundle."
  value       = aws_apigatewayv2_api.api.api_endpoint

  # The API's CORS origin is pinned in infra/api.tf (to avoid a dependency cycle). An
  # output is a leaf node, so checking it here adds no cycle, and it fails the plan.
  precondition {
    condition     = local.frontend_cloudfront_domain == aws_cloudfront_distribution.frontend.domain_name
    error_message = "local.frontend_cloudfront_domain in infra/api.tf is not the frontend distribution's domain, so the API's CORS would block the frontend. Set it to `terraform output -raw frontend_distribution_domain`."
  }
}

output "events_http_dns" {
  description = "Host name of the silvercare-events AppSync Event API's HTTP endpoint (frontend VITE_EVENTS_URL = https://<this>/event). Not a secret: it ships in the bundle."
  value       = local.events_http_dns
}

output "events_realtime_dns" {
  description = "Host name of the silvercare-events AppSync Event API's WebSocket endpoint (CSP connect-src)."
  value       = local.events_realtime_dns
}
