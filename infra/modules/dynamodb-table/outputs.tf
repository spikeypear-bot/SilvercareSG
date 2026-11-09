output "name" {
  description = "Table name, for Lambda environment variables."
  value       = aws_dynamodb_table.this.name
}

output "arn" {
  description = "Table ARN, for IAM policies. Index ARNs are \"<arn>/index/<index-name>\"."
  value       = aws_dynamodb_table.this.arn
}

output "read_capacity" {
  description = "Provisioned read capacity units of the table (not its indexes)."
  value       = aws_dynamodb_table.this.read_capacity
}

output "write_capacity" {
  description = "Provisioned write capacity units of the table (not its indexes)."
  value       = aws_dynamodb_table.this.write_capacity
}
