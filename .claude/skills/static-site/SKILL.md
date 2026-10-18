---
name: static-site
description: Host the React frontend on S3 + CloudFront with Terraform - private bucket, origin access control, SPA routing, security headers, caching, and deployment. Use when setting up or changing CloudFront, the frontend bucket, a custom domain, or frontend deploys.
---

# Static site hosting (S3 + CloudFront)

Follow `tf-change` and `iam-policy` for all Terraform.

## Bucket

- Private: block all public access, `BucketOwnerEnforced`, encrypted, no website hosting
  endpoint (CloudFront reads through the REST endpoint).
- Name: `silvercare-frontend-<account_id>`.
- Bucket policy allows only `cloudfront.amazonaws.com` to read (`s3:GetObject`), with the
  condition `AWS:SourceArn` = this distribution's ARN.

## Distribution

- `aws_cloudfront_origin_access_control` (OAC, signing `always`, `sigv4`). Do not use the
  legacy OAI.
- `default_root_object = "index.html"`.
- **SPA routing:** `custom_error_response` mapping 403 and 404 to `/index.html` with a
  200 response, so deep links like `/residents/abc` load the app.
- `viewer_protocol_policy = "redirect-to-https"`, minimum TLS 1.2.
- `price_class = "PriceClass_200"`. `PriceClass_100` has no Asian edge locations, so
  Singapore users would be served from far away.
- A response headers policy (`aws_cloudfront_response_headers_policy`) with HSTS,
  `X-Content-Type-Options`, `X-Frame-Options: DENY`, a strict `Referrer-Policy`, and a
  Content-Security-Policy. The CSP's `connect-src` must allow the API URL and
  `https://cognito-idp.ap-southeast-1.amazonaws.com`.
- **Caching:** hashed assets (`/assets/*`) get a long cache with `immutable`.
  `index.html` gets `no-cache`, so new deploys show up immediately.
- Custom domain (if any): the ACM certificate must be in `us-east-1`, which needs an
  aliased AWS provider. Ask the user for the domain; don't invent one.

## Outputs

Output the distribution domain, distribution ID, and bucket name. The API's CORS
settings and the frontend's build config need them.

## Deploying the build

`deploy.yml` builds and uploads the frontend on every push to `main`, after
`terraform apply`, in the same job:

1. Build with `VITE_USER_POOL_ID` and `VITE_USER_POOL_CLIENT_ID` read from
   `terraform output -raw` (never printed).
2. `s3 sync dist/assets/` with `Cache-Control: public, max-age=31536000, immutable`.
3. `s3 sync dist/` (excluding `assets/`) with `--delete` and `Cache-Control: no-cache`.
4. `s3 sync dist/assets/ --delete` to remove old hashed files, then invalidate
   `/index.html`.

Terraform doesn't upload app files: every build changes file names, which would flood the
plan. When the build needs a new Terraform output (e.g. the API URL), add it to the
"Build frontend" step. Changing `.github/` needs the user to ask for it.
