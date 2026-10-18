locals {
  # DynamoDB only needs attribute definitions for attributes used as a key in the table
  # or an index. Derive them from the keys so the list can't drift. All keys are strings.
  key_attributes = distinct(concat(
    [var.hash_key, var.range_key],
    flatten([for gsi in var.global_secondary_indexes : [gsi.hash_key, gsi.range_key]]),
  ))
}

resource "aws_dynamodb_table" "this" {
  #checkov:skip=CKV_AWS_119:Encrypted at rest with the AWS owned key (free). A customer-managed KMS key costs ~US$1/month per key; see Settings in docs/data-model.md.
  #checkov:skip=CKV2_AWS_16:Fixed provisioned capacity inside the always-free 25 RCU/25 WCU on purpose. Auto scaling adds CloudWatch alarm cost and could scale past the free tier; see Capacity in docs/data-model.md.
  name                        = var.name
  billing_mode                = "PROVISIONED"
  read_capacity               = var.read_capacity
  write_capacity              = var.write_capacity
  hash_key                    = var.hash_key
  range_key                   = var.range_key
  deletion_protection_enabled = true

  # No server_side_encryption block: DynamoDB always encrypts at rest, and without the
  # block it uses the AWS owned key, which is free.

  dynamic "attribute" {
    for_each = local.key_attributes
    content {
      name = attribute.value
      type = "S"
    }
  }

  dynamic "global_secondary_index" {
    for_each = var.global_secondary_indexes
    content {
      name               = global_secondary_index.value.name
      hash_key           = global_secondary_index.value.hash_key
      range_key          = global_secondary_index.value.range_key
      projection_type    = length(global_secondary_index.value.non_key_attributes) > 0 ? "INCLUDE" : "KEYS_ONLY"
      non_key_attributes = length(global_secondary_index.value.non_key_attributes) > 0 ? global_secondary_index.value.non_key_attributes : null
      read_capacity      = global_secondary_index.value.read_capacity
      write_capacity     = global_secondary_index.value.write_capacity
    }
  }

  dynamic "ttl" {
    for_each = var.ttl_attribute == null ? [] : [var.ttl_attribute]
    content {
      attribute_name = ttl.value
      enabled        = true
    }
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = var.tags
}
