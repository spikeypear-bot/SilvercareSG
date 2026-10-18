---
status: ready          # draft | ready | built
---

# Frontend hosting

## Summary

Host the static React build on a private S3 bucket behind a CloudFront distribution, so
the app is reachable over HTTPS at the distribution's default `*.cloudfront.net` domain.
No custom domain and no WAF in this feature.

## Users and access

| Group | Can see | Can do |
|---|---|---|
| doctor | the public app shell (static files) | load the app |
| nurse | the public app shell (static files) | load the app |
| caregiver | the public app shell (static files) | load the app |
| family | the public app shell (static files) | load the app |

The static files contain no personal data and are public by design: anyone with the URL
can download the JavaScript bundle. Access to data is decided by Cognito and (later) the
API, not by hosting. Only CloudFront can read the bucket.

## Acceptance criteria

1. Given the Terraform in `infra/`, when `make tf-check` runs, then fmt, validate,
   tflint, and checkov pass (any checkov skip has an inline reason, e.g. WAF deferred by
   the user, no custom domain so no custom certificate, access logging deferred for cost).
2. The bucket is named `silvercare-frontend-<account_id>` (account ID from
   `data.aws_caller_identity`), blocks all public access, uses `BucketOwnerEnforced`,
   SSE-S3 (AES256) encryption, has versioning disabled, and has no website configuration.
3. The bucket policy allows only the `cloudfront.amazonaws.com` service principal to
   `s3:GetObject` on `<bucket>/*`, with condition `AWS:SourceArn` equal to this
   distribution's ARN, and denies any request where `aws:SecureTransport` is false.
4. The distribution uses an `aws_cloudfront_origin_access_control` (origin type `s3`,
   signing behaviour `always`, protocol `sigv4`) against the bucket's regional REST
   endpoint (`bucket_regional_domain_name`). No legacy OAI.
5. The distribution uses the default CloudFront certificate
   (`cloudfront_default_certificate = true`), `default_root_object = "index.html"`,
   `price_class = "PriceClass_200"`, `http_version = "http2and3"`, IPv6 enabled,
   no aliases, no WAF (`web_acl_id` unset), and `viewer_protocol_policy =
   "redirect-to-https"` on every behaviour. Allowed methods are `GET`, `HEAD`, `OPTIONS`.
6. SPA routing: `custom_error_response` maps origin `403` and `404` to
   `/index.html` with response code `200` and `error_caching_min_ttl = 0`.
7. Caching: the default behaviour uses the AWS managed `CachingDisabled` cache policy
   (so `index.html` is always fresh after a deploy); an ordered behaviour for
   `/assets/*` uses the AWS managed `CachingOptimized` cache policy. Both use compression.
   Managed policies are looked up with `data "aws_cloudfront_cache_policy"` by name.
8. A custom `aws_cloudfront_response_headers_policy` named
   `silvercare-frontend-security` is attached to both behaviours, with:
   - HSTS: `max-age=63072000; includeSubDomains`, override on (no `preload`, since there
     is no custom domain)
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - Content-Security-Policy, override on, exactly:
     `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
     font-src 'self'; connect-src 'self' https://cognito-idp.ap-southeast-1.amazonaws.com;
     frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'`
     (one line; the API URL is added to `connect-src` when the API exists)
   - a custom header `Permissions-Policy: camera=(), microphone=(), geolocation=()`
9. Outputs in `infra/outputs.tf`: `frontend_bucket_name`, `frontend_distribution_id`,
   `frontend_distribution_domain` (the `*.cloudfront.net` domain), and `frontend_url`
   (`https://<domain>`).
10. The resources live in `infra/frontend_hosting.tf` (or a module under
    `infra/modules/static-site/` called from it), and carry the provider default tags.

## Data

- Reads: none
- Writes: none
- New access patterns or tables: none. The bucket holds only the public static build
  (HTML, JS, CSS, fonts, images), never personal data.

## IoT

None.

## API contract

None (no API in this feature).

## Screens

None. Uploading the build to the bucket is done by the user after `terraform apply`
(see Out of scope).

## Alerts and notifications

None.

## Privacy

- Personal data touched: none. The bucket must only ever contain the static build.
- Retention: not applicable. Each deploy replaces the files (`s3 sync --delete`).
- Anything that must not be logged or shown: no CloudFront access logs in this feature
  (they would contain viewer IP addresses; logging is deferred and needs its own
  retention decision).

## Out of scope

- Custom domain, ACM certificate, Route 53.
- AWS WAF (to be added later by the user's decision).
- CloudFront access logging and monitoring alarms.
- Uploading the build and invalidating the cache from CI. This needs extra permissions
  on the CI role (`s3:ListBucket`, `s3:PutObject`, `s3:DeleteObject` on the bucket,
  `cloudfront:CreateInvalidation` on the distribution); the builder must not change the
  CI role or `.github/`, only report what is needed.
- The React app itself (see `docs/features/sign-in.md`).

## Open questions

Must be empty before `status: ready`.
