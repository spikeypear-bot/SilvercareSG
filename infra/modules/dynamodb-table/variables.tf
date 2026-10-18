variable "name" {
  description = "Table name, e.g. silvercare-registry. Changing it destroys and recreates the table."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9_.-]{3,255}$", var.name))
    error_message = "Table names are 3-255 characters: letters, digits, '_', '-' and '.'."
  }
}

variable "hash_key" {
  description = "Partition key attribute (string). Changing it destroys and recreates the table."
  type        = string
}

variable "range_key" {
  description = "Sort key attribute (string). Changing it destroys and recreates the table."
  type        = string
}

variable "read_capacity" {
  description = "Fixed read capacity units for the table (no auto scaling)."
  type        = number

  validation {
    condition     = var.read_capacity >= 1 && floor(var.read_capacity) == var.read_capacity
    error_message = "read_capacity must be a whole number of at least 1."
  }
}

variable "write_capacity" {
  description = "Fixed write capacity units for the table (no auto scaling)."
  type        = number

  validation {
    condition     = var.write_capacity >= 1 && floor(var.write_capacity) == var.write_capacity
    error_message = "write_capacity must be a whole number of at least 1."
  }
}

variable "global_secondary_indexes" {
  description = <<-EOT
    Global secondary indexes. Each one projects the index keys plus the listed
    non_key_attributes (INCLUDE), or only the keys (KEYS_ONLY) if the list is empty.
    Projecting ALL attributes is not supported on purpose: copy only what a query needs.
  EOT
  type = list(object({
    name               = string
    hash_key           = string
    range_key          = string
    non_key_attributes = list(string)
    read_capacity      = number
    write_capacity     = number
  }))
  default = []

  validation {
    condition = alltrue([
      for gsi in var.global_secondary_indexes :
      gsi.read_capacity >= 1 && gsi.write_capacity >= 1 &&
      floor(gsi.read_capacity) == gsi.read_capacity && floor(gsi.write_capacity) == gsi.write_capacity
    ])
    error_message = "Each index needs whole-number read_capacity and write_capacity of at least 1."
  }
}

variable "ttl_attribute" {
  description = "Attribute holding the expiry time (epoch seconds) for DynamoDB TTL, or null for no TTL."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags for the table. Provider default_tags (project, managed_by) are added automatically."
  type        = map(string)
  default     = {}
}
