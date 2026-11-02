# Root input variables. CI passes them as TF_VAR_* from GitHub (docs/features/
# alerts-core.md, criterion 25): subscriber_emails from the repository secret
# SUBSCRIBER_EMAILS, the other two from the repository variables ALERT_SUBSCRIBERS and
# OPS_SUBSCRIBERS. Each defaults to empty, so tf-check and local validation work without
# them (then no email subscriptions are created).
#
# Labels are the keys everywhere and appear in resource addresses in the public plan
# comment, so they must not be people's names (e.g. "family-c1", not a first name).

variable "subscriber_emails" {
  description = "Label => email address of every alert or ops email subscriber. Sensitive: never a for_each key."
  type        = map(string)
  default     = {}
  sensitive   = true

  validation {
    condition     = alltrue([for label in keys(var.subscriber_emails) : can(regex("^[a-z0-9-]{1,32}$", label))])
    error_message = "subscriber_emails labels must match ^[a-z0-9-]{1,32}$."
  }

  validation {
    condition     = alltrue([for email in values(var.subscriber_emails) : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", email))])
    error_message = "Every subscriber_emails value must be an email address."
  }
}

variable "alert_subscribers" {
  description = "Label => the resident IDs whose alerts that subscriber is emailed. Every label needs an entry in subscriber_emails."
  type = map(object({
    resident_ids = list(string)
  }))
  default = {}

  validation {
    condition     = alltrue([for label in keys(var.alert_subscribers) : can(regex("^[a-z0-9-]{1,32}$", label))])
    error_message = "alert_subscribers labels must match ^[a-z0-9-]{1,32}$."
  }

  validation {
    condition = alltrue([
      for s in values(var.alert_subscribers) :
      length(s.resident_ids) > 0 && alltrue([for id in s.resident_ids : can(regex("^res-[0-9]{4}$", id))])
    ])
    error_message = "Each alert_subscribers entry needs at least one resident ID, each matching ^res-[0-9]{4}$."
  }

  # Labels aren't secret; nonsensitive() keeps the condition result usable.
  validation {
    condition     = alltrue([for label in keys(var.alert_subscribers) : contains(nonsensitive(keys(var.subscriber_emails)), label)])
    error_message = "Every alert_subscribers label needs an entry in subscriber_emails."
  }
}

variable "ops_subscribers" {
  description = "Labels of the team members emailed by the DLQ alarms. Every label needs an entry in subscriber_emails."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for label in var.ops_subscribers : can(regex("^[a-z0-9-]{1,32}$", label))])
    error_message = "ops_subscribers labels must match ^[a-z0-9-]{1,32}$."
  }

  validation {
    condition     = alltrue([for label in var.ops_subscribers : contains(nonsensitive(keys(var.subscriber_emails)), label)])
    error_message = "Every ops_subscribers label needs an entry in subscriber_emails."
  }
}
