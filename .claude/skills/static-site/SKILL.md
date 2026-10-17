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

The build is uploaded after `terraform apply`, not by Terraform:

```sh
cd frontend && npm ci && npm run build
aws s3 sync dist/ s3://<bucket>/ --delete
aws cloudfront create-invalidation --distribution-id <id> --paths "/index.html"
```

Adding these steps to `.github/workflows/deploy.yml` needs extra CI role permissions
(`s3:ListBucket`, `s3:PutObject`, `s3:DeleteObject` on the frontend bucket, and
`cloudfront:CreateInvalidation` on the distribution). Tell the user what to add. Don't
change the CI role yourself.
