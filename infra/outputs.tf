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
