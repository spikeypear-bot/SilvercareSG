output "function_name" {
  description = "Lambda function name."
  value       = aws_lambda_function.this.function_name
}

output "function_arn" {
  description = "Lambda function ARN, for invoke permissions and IoT rule actions."
  value       = aws_lambda_function.this.arn
}

output "role_arn" {
  description = "ARN of the function's own IAM role."
  value       = aws_iam_role.this.arn
}

output "log_group_name" {
  description = "Name of the function's CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.name
}
