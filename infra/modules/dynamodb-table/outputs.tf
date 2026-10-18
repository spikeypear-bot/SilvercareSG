output "name" {
  description = "Table name, for Lambda environment variables."
  value       = aws_dynamodb_table.this.name
}

output "arn" {
  description = "Table ARN, for IAM policies. Index ARNs are \"<arn>/index/<index-name>\"."
  value       = aws_dynamodb_table.this.arn
}
