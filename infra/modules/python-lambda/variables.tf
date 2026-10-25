variable "function_name" {
  description = "Lambda function name, e.g. silvercare-telemetry. Also names its role and log group."
  type        = string

  validation {
    condition     = can(regex("^silvercare-[a-z0-9-]{1,53}$", var.function_name))
    error_message = "function_name must be silvercare-<kebab-case>, at most 64 characters (the role name limit)."
  }
}

variable "description" {
  description = "Short description of what the function does."
  type        = string
}

variable "handler" {
  description = "Entry point, <module>.<function>, e.g. handler.telemetry_handler."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*$", var.handler))
    error_message = "handler must be <module>.<function>."
  }
}

variable "zip_path" {
  description = "Path of the deployment zip (an archive_file output_path)."
  type        = string
}

variable "zip_hash" {
  description = "Base64 SHA-256 of the zip (archive_file output_base64sha256), so code changes redeploy."
  type        = string
}

variable "layers" {
  description = "Layer ARNs with pinned versions, e.g. the public Powertools layer."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for l in var.layers : can(regex("^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:layer:[A-Za-z0-9_-]+:[0-9]+$", l))])
    error_message = "Each layer must be a full layer version ARN ending in :<version>."
  }
}

variable "environment" {
  description = "Environment variables (no secrets: they are visible in the console and plan)."
  type        = map(string)
  default     = {}
}

variable "memory_size" {
  description = "Memory in MB."
  type        = number
  default     = 128
}

variable "timeout" {
  description = "Timeout in seconds."
  type        = number
  default     = 10
}

variable "log_retention_days" {
  description = "Retention of the function's CloudWatch log group, in days."
  type        = number
  default     = 30
}

variable "policy_json" {
  description = <<-EOT
    IAM policy document (JSON, from data "aws_iam_policy_document") with what the code
    itself needs. The module adds writing to its own log group and sending to the
    on-failure destination, and puts everything in the role's one inline policy.
  EOT
  type        = string
}

variable "on_failure_destination" {
  description = <<-EOT
    Where async invocations that still fail after the retries go: { arn = <SQS queue
    ARN> }. Leave null for functions that aren't invoked asynchronously (e.g. fed by an
    SQS event source mapping): then no async invoke config is created. An object rather
    than a plain ARN so that whether it is set is known at plan time, even before the
    queue exists (count can't depend on an unknown ARN).
  EOT
  type = object({
    arn = string
  })
  default = null

  validation {
    condition     = var.on_failure_destination == null || can(regex("^arn:aws:sqs:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]+$", var.on_failure_destination.arn))
    error_message = "on_failure_destination.arn must be an SQS queue ARN."
  }
}

variable "maximum_retry_attempts" {
  description = "Async invoke retries after the first failure (0-2). Used only with on_failure_destination."
  type        = number
  default     = 2

  validation {
    condition     = contains([0, 1, 2], var.maximum_retry_attempts)
    error_message = "maximum_retry_attempts must be 0, 1, or 2."
  }
}

variable "maximum_event_age_in_seconds" {
  description = "How long an async event may wait before it goes to the on-failure destination (60-21600). Used only with on_failure_destination."
  type        = number
  default     = 3600

  validation {
    condition     = var.maximum_event_age_in_seconds >= 60 && var.maximum_event_age_in_seconds <= 21600
    error_message = "maximum_event_age_in_seconds must be between 60 and 21600."
  }
}

variable "tags" {
  description = "Extra tags. Provider default_tags (project, managed_by) are added automatically."
  type        = map(string)
  default     = {}
}
