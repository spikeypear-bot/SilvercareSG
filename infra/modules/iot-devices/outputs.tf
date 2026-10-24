output "device_certificates" {
  description = "Map of device ID to its certificate PEM. Certificates are public (the private keys never leave the developer's machine), but the output stays sensitive so applies don't print it in CI logs."
  value       = { for id, c in aws_iot_certificate.device : id => c.certificate_pem }
  sensitive   = true
}

output "thing_group_arns" {
  description = "Map of static thing group name (wearables, bedroom-sensors, canary-wearables, canary-bedroom-sensors) to its ARN, for job targets."
  value       = { for name, g in aws_iot_thing_group.this : name => g.arn }
}
