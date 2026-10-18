# Cognito user pool, groups, and the React app client (docs/features/sign-in.md).
#
# Users are data, not Terraform resources: admins create them in the Cognito console and
# add them to one of the groups below. Groups only select the kind of user; which
# residents a caregiver/family user may see is decided by links in DynamoDB (see
# docs/data-model.md), checked by the API.
#
# Lite tier with the default Cognito email sender: no fixed cost, and within the free
# monthly active user allowance. Changing the schema or username settings replaces the
# pool (and deletes every user), which deletion protection blocks.

resource "aws_cognito_user_pool" "users" {
  name = "silvercare-users"

  user_pool_tier      = "LITE"
  deletion_protection = "ACTIVE"

  # Sign in with email. auto_verified_attributes does NOT verify admin-created users: when
  # creating a user, admins must tick "Mark email address as verified"
  # (email_verified=true), or the user can never reset a forgotten password (recovery is
  # verified email only).
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Treat "Test.User@example.com" and "test.user@example.com" as the same account.
  username_configuration {
    case_sensitive = false
  }

  # Keep the old (verified) email active until a changed email is verified. This also
  # applies to admins: change `email` and set email_verified=true in the same edit.
  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  # No self sign-up; accounts are created by an admin only (CLAUDE.md, open decision 3).
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # Optional authenticator-app (TOTP) MFA. No SMS MFA, so no sms_configuration.
  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Default Cognito sender, no SES.
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # No schema blocks: email and name are standard attributes and deliberately NOT
  # required, because Cognito lets every app client write required attributes. Leaving
  # them optional is what lets the app client below make them read-only, so only admins
  # can change a user's name or email (decided by the user; the name will identify who
  # acknowledged an alert). Admins set both when creating a user. No custom attributes,
  # no NRIC, no health data. Required-ness can't be changed after the pool is created.
  # Never add schema blocks for email or name later: that forces the pool to be replaced.

  # Fail at plan time (visible in the PR comment) rather than at apply, before any
  # replacement could delete every user. deletion_protection is the second guard.
  lifecycle {
    prevent_destroy = true
  }
}

# doctor and nurse together are "staff". No IAM roles: groups only drive views and the
# API's group checks.
resource "aws_cognito_user_group" "doctor" {
  user_pool_id = aws_cognito_user_pool.users.id
  name         = "doctor"
  description  = "Staff doctors: staff dashboard with every resident and all open alerts."
}

resource "aws_cognito_user_group" "nurse" {
  user_pool_id = aws_cognito_user_pool.users.id
  name         = "nurse"
  description  = "Staff nurses: staff dashboard with every resident and all open alerts."
}

resource "aws_cognito_user_group" "caregiver" {
  user_pool_id = aws_cognito_user_pool.users.id
  name         = "caregiver"
  description  = "Caregivers: family view of only the residents linked to them."
}

resource "aws_cognito_user_group" "family" {
  user_pool_id = aws_cognito_user_pool.users.id
  name         = "family"
  description  = "Family members: family view of only the residents linked to them."
}

# Public client for the browser app (it can't keep a secret). SRP sign-in only, no
# OAuth or hosted UI.
resource "aws_cognito_user_pool_client" "web" {
  name         = "silvercare-web"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 7

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  allowed_oauth_flows_user_pool_client = false

  read_attributes = ["email", "email_verified", "name"]
  # Users must not change their own name or email (admins do, via AdminUpdateUserAttributes).
  # An empty or unset list means "all attributes writable" in Cognito, so this lists one
  # attribute the app never reads or shows instead. Every app client added to this pool
  # must set write_attributes the same way: without name or email, and never [].
  write_attributes = ["zoneinfo"]
}
