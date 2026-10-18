# Monthly AWS cost budget with email alerts.
#
# Lives in bootstrap/ (applied once by hand, never by CI) because the alert email must
# not be committed to this public repo. Set it in bootstrap/terraform.tfvars, which is
# git-ignored (copy terraform.tfvars.example).
#
# Budgets is a global billing service: it reads cost data only, no resident data.
# Plain alerting budgets (no budget actions) are free.

variable "budget_alert_email" {
  description = "Email address that receives budget alerts. Set in terraform.tfvars; never commit it."
  type        = string
  sensitive   = true
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD. US$120 of credits must last until the end of November 2026."
  type        = number
  default     = 40
}

resource "aws_budgets_budget" "monthly" {
  name         = "silvercare-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Track usage before credits are applied, so alerts show what is actually being used
  # up, not the US$0 the bill shows while credits last.
  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_alert_email]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
