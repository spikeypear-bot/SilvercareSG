variable "devices" {
  description = "The device list (infra/devices/devices.json): one object per device with id (also the thing name and MQTT client ID), type (wearable or bedroom), and canary (true puts it in its type's canary group)."
  type = list(object({
    id     = string
    type   = string
    canary = bool
  }))

  validation {
    condition = alltrue([
      for d in var.devices : can(regex("^(wearable|bedroom)-[0-9]{4}$", d.id))
    ])
    error_message = "Invalid device id(s) in devices.json (must match ^(wearable|bedroom)-[0-9]{4}$): ${join(", ", [for d in var.devices : format("%q", d.id) if !can(regex("^(wearable|bedroom)-[0-9]{4}$", d.id))])}."
  }

  validation {
    condition = alltrue([
      for d in var.devices : contains(["wearable", "bedroom"], d.type) && startswith(d.id, "${d.type}-")
    ])
    error_message = "Device(s) in devices.json whose type is not wearable/bedroom or does not match the id prefix: ${join(", ", [for d in var.devices : format("%q (type %q)", d.id, d.type) if !(contains(["wearable", "bedroom"], d.type) && startswith(d.id, "${d.type}-"))])}."
  }

  validation {
    condition     = length(distinct([for d in var.devices : d.id])) == length(var.devices)
    error_message = "Duplicate device id(s) in devices.json: ${join(", ", [for id in distinct([for d in var.devices : d.id]) : format("%q", id) if length([for d in var.devices : d.id if d.id == id]) > 1])}."
  }
}

variable "csr_dir" {
  description = "Directory holding one committed CSR per device, named <id>.csr (created by scripts/device-keys.py). Relative paths are relative to the root config."
  type        = string
}

variable "tags" {
  description = "Tags applied to every taggable resource (thing types, thing groups, policies)."
  type        = map(string)
  default     = {}
}
