# Frontend hosting (docs/features/frontend-hosting.md): the static React build in a
# private S3 bucket, served over HTTPS by CloudFront at its default *.cloudfront.net
# domain. No custom domain, no WAF, no access logging in this feature.
#
# The bucket holds only the public static build (HTML, JS, CSS, fonts, images), never
# personal data. The build is uploaded after apply (s3 sync --delete), not by Terraform.

data "aws_caller_identity" "current" {}

# AWS managed cache policies, looked up by name.
data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

# ---------------------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------------------

resource "aws_s3_bucket" "frontend" {
  #checkov:skip=CKV_AWS_18:Access logging deferred by the user (cost, and logs would hold viewer IP addresses that need a retention decision first).
  #checkov:skip=CKV_AWS_21:Versioning off on purpose. The bucket holds only the rebuildable static build; every deploy replaces it (s3 sync --delete), and old versions would only add storage cost.
  #checkov:skip=CKV_AWS_144:No cross-region replication. The static build can be rebuilt from git; replication adds cost and copies data out of ap-southeast-1.
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256) is used on purpose. The content is a public static build, and a customer-managed KMS key costs ~US$1/month plus request charges on every CloudFront fetch.
  #checkov:skip=CKV2_AWS_62:No event notifications needed. Nothing reacts to uploads of the static build.
  #checkov:skip=CKV2_AWS_61:No lifecycle rules needed. Versioning is off and each deploy replaces the files, so nothing accumulates.
  bucket = "silvercare-frontend-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Only this distribution (through OAC) may read objects; any request without TLS is denied.
data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid       = "AllowCloudFrontRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.frontend.arn]
    }
  }

  # Deny statement: s3:* and principal * are intentional here. Denying every action from
  # every caller without TLS is narrower access, not broader.
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.frontend.arn,
      "${aws_s3_bucket.frontend.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket.json

  # Apply after the public access block so the policy is never evaluated without it.
  depends_on = [aws_s3_bucket_public_access_block.frontend]
}

# ---------------------------------------------------------------------------------------
# CloudFront
# ---------------------------------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "silvercare-frontend"
  description                       = "CloudFront access to the silvercare-frontend bucket."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_response_headers_policy" "frontend_security" {
  #checkov:skip=CKV_AWS_259:HSTS is enforced (2 years, includeSubDomains, override on) but without preload, since there is no custom domain to put on the preload list.
  name    = "silvercare-frontend-security"
  comment = "Security headers for the SilverCare frontend."

  security_headers_config {
    # No preload: there is no custom domain to put on the preload list.
    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      preload                    = false
      override                   = true
    }

    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    # Add the API URL to connect-src once the API exists.
    content_security_policy {
      content_security_policy = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' https://cognito-idp.ap-southeast-1.amazonaws.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
      override                = true
    }
  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=()"
      override = true
    }
  }
}

resource "aws_cloudfront_distribution" "frontend" {
  #checkov:skip=CKV_AWS_68:WAF deferred by the user; it will be added in a later change (a web ACL costs ~US$5/month plus rules).
  #checkov:skip=CKV2_AWS_47:WAF deferred by the user, so there is no web ACL to carry the Log4j managed rule yet.
  #checkov:skip=CKV_AWS_86:Access logging deferred by the user (cost, and logs would hold viewer IP addresses that need a retention decision first).
  #checkov:skip=CKV_AWS_174:No custom domain, so the default *.cloudfront.net certificate is used and CloudFront does not allow setting a minimum TLS version for it.
  #checkov:skip=CKV2_AWS_42:No custom domain yet, so no custom ACM certificate; the default CloudFront certificate is used.
  #checkov:skip=CKV_AWS_310:Single static origin. Origin failover would need a second bucket and replication, which the spec does not ask for.
  #checkov:skip=CKV_AWS_374:No geo restriction. Family members may open the app from outside Singapore.
  enabled             = true
  comment             = "SilverCare frontend"
  default_root_object = "index.html"
  price_class         = "PriceClass_200"
  http_version        = "http2and3"
  is_ipv6_enabled     = true

  origin {
    origin_id                = "silvercare-frontend-s3"
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  # index.html and any other non-hashed file: never cached, so deploys show up at once.
  default_cache_behavior {
    target_origin_id           = "silvercare-frontend-s3"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_disabled.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.frontend_security.id
  }

  # Hashed build assets: content-addressed, so a long cache is safe.
  ordered_cache_behavior {
    path_pattern               = "/assets/*"
    target_origin_id           = "silvercare-frontend-s3"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.frontend_security.id
  }

  # SPA routing: deep links (S3 returns 403 for missing keys under OAC) load the app.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
