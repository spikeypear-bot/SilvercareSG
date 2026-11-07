---
status: ready          # draft | ready | built
---

# Tableau access

## Summary

Gives Tableau Desktop read-only, live access to the curated layer through Athena, so
the Track D analytics dashboards can be built on the daily resident summaries
(`silvercare_lake.daily_summaries`, docs/features/curated-layer.md). Tableau connects
with its Amazon Athena connector as a dedicated read-only IAM user, defined in
Terraform, in its own Athena workgroup. No portal screens, API routes, or Lambdas
change.

## Decisions

Made by the user (2026-11-07, during the curated-layer spec):

- Tableau connects as a **new read-only IAM user** (not the shared developer user).
- The tool is **Tableau Desktop, free edition**, which has the Amazon Athena connector.
  Tableau Public is not used (it publishes data publicly).
- Doctors use the portal, not Tableau (docs/features/nightly-results.md).
- The shared developer user can already query `daily_summaries` in the
  `silvercare-analysis` workgroup (curated-layer.md); that doesn't change here.

Made by the user (2026-11-08, this spec):

- Tableau reads **`daily_summaries` only**, not the clean tables, raw telemetry, or
  `rejected`.
- The IAM user, its policy, and its Lake Formation grants are **defined in Terraform**
  (CLAUDE.md: all AWS resources in Terraform). Its **access key is created by hand** by
  the account owner, so the secret never enters Terraform state, the repo, or CI.
- **Live connection only:** Tableau queries Athena each time a dashboard opens or
  refreshes. No extracts (`.hyper` files) or packaged workbooks (`.twbx`) are saved,
  which keeps data on the laptop to a minimum. Tableau still keeps a query cache on
  disk, and a `.twb` can hold some values (filter lists, aliases, groups); acceptable
  while all data is synthetic, and part of the "real resident data" decision below.
- **Edition checked (2026-11-08):** the Track D owner's Tableau Desktop (free edition)
  has the Amazon Athena connector and saves workbooks locally as `.twb` without
  signing in to Tableau Public, so it isn't Tableau Desktop Public Edition (raised by
  the privacy review).
- **Never publish:** no signing in to or publishing to Tableau Public, Tableau Cloud,
  or Tableau Server with this connection; workbooks stay as local `.twb` files on the
  Track D owner's laptop.
- **One user to start, `silvercare-tableau-1`, used only by the Track D owner.** Users
  are a list in Terraform, so a teammate can be added later with a one-line change.
  Names are neutral (no personal names: the repo and its plan comments are public);
  who holds which user is kept off the repo.
- **Real data later:** "Decide whether Tableau users' laptops may display real resident
  data" is added to "Before any real resident data arrives" in `docs/data-model.md`
  (criterion 10).

## Users and access

| Who | Can see | Can do |
|---|---|---|
| Tableau IAM users (`silvercare-tableau-1`) | `silvercare_lake.daily_summaries` only, through Athena and Lake Formation | run Athena queries in the `silvercare-tableau` workgroup; nothing else |
| Shared developer user | unchanged | unchanged |
| Portal groups (doctor, nurse, caregiver, ops) | nothing new | nothing new |

How access to a specific resident is decided: not applicable. A Tableau user reads the
whole `daily_summaries` table, keyed by the opaque `resident_id`; the curated layer
holds no names or contact details.

## Acceptance criteria

### Users

1. A new file `infra/tableau.tf` defines `local.tableau_users`, a set of user names,
   starting with exactly `["silvercare-tableau-1"]`. Every resource below that is per
   user uses `for_each` over this set, so adding a name adds that user's IAM user,
   group membership, and Lake Formation grants and nothing else.
2. One `aws_iam_user` per name, with **no path** (its ARN is the Lake Formation
   principal, and AWS documents only plain `user/<name>` ARNs there; changed after
   review, 2026-11-08), `force_destroy = false` (a
   user whose key was made by hand can't be deleted by Terraform until the owner
   deletes the key, so a removal can't silently orphan a live key). No
   `aws_iam_access_key`, no login profile (no console password), no inline policy on
   the user.
3. An IAM group `silvercare-tableau` holds the permissions (one inline policy, criterion
   5); each Tableau user is a member through `aws_iam_user_group_membership`.
4. A header comment in `infra/tableau.tf` states: purpose, that access keys are created
   by hand by the account owner and never stored in the repo, chat, CI, or Terraform
   state, live connection only, and that user names must stay neutral.

### Permissions (IAM, least privilege)

5. The group's policy allows only:
   - **Athena, own workgroup:** `athena:StartQueryExecution`, `StopQueryExecution`,
     `GetQueryExecution`, `GetQueryResults`, `GetQueryResultsStream`,
     `BatchGetQueryExecution`, `ListQueryExecutions`, `GetWorkGroup` on
     `workgroup/silvercare-tableau` only.
   - **Athena, listing:** `athena:ListWorkGroups` on `*` (AWS supports only `*`).
   - **Athena, catalog browsing:** `athena:GetDataCatalog`, `ListDatabases`,
     `GetDatabase`, `ListTableMetadata`, `GetTableMetadata` on
     `datacatalog/AwsDataCatalog`.
   - **Glue, read:** `glue:GetDatabase`, `GetDatabases`, `GetTable`, `GetTables`,
     `GetPartition`, `GetPartitions`, `BatchGetPartition` on the catalog, the
     `silvercare_lake` database, and `table/silvercare_lake/*`; `glue:GetDatabase` on
     `database/default`. The table wildcard is needed for `GetTables` (listing); Lake
     Formation filters the results to the tables the caller has permissions on
     (criterion 7), so only `daily_summaries` is listed or readable. A comment says so.
   - **Lake Formation:** `lakeformation:GetDataAccess` on `*` (AWS supports only `*`).
   - **Athena results, own prefix:** `s3:PutObject`, `s3:GetObject`,
     `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts` on
     `silvercare-athena-results-<account_id>/tableau/*`; `s3:ListBucket` on that bucket
     with `StringLikeIfExists` `s3:prefix` `tableau` / `tableau/*` (the same form as the
     summaries role's `ListOwnResults`; a list without a prefix can show other
     workgroups' result keys, which are query IDs, not data, and `GetObject` stays
     limited to `tableau/*`); `s3:GetBucketLocation` on that bucket.
6. Nothing else: no direct S3 access to the history or clean buckets (Lake Formation
   vends the data access), no other Athena workgroups, no DynamoDB, no IAM, no Lambda,
   no write to the Glue catalog.

### Lake Formation

7. For each Tableau user: `DESCRIBE` on the `default` and `silvercare_lake` databases,
   and `DESCRIBE` and `SELECT` on `silvercare_lake.daily_summaries`, as the developer
   grants in `infra/summaries.tf` (`developer_daily_summaries`). No grants on any other
   table (`vitals` etc., `rejected`, `raw_telemetry`). Resource addresses use the user
   name as the `for_each` key (neutral by criterion 1).

### Athena workgroup

8. A `tableau` entry in `local.athena_workgroups` (`infra/clean.tf`):
   `{ name = "silvercare-tableau", prefix = "tableau" }`, so it gets the same settings
   as the others: engine 3, enforced configuration, 1 GB scan limit per query, results
   under `tableau/` in `silvercare-athena-results-<account_id>`, encrypted SSE-S3. The
   bucket's existing lifecycle rule (no prefix filter) expires the results after 7 days.

### Setup guide and docs

9. A new `docs/tableau.md` (for the Track D owner and any teammate added later;
   written outside the build workflow, whose builders may only edit
   `docs/data-model.md` and `docs/metrics.md`):
   - Install Tableau Desktop (free edition) and the Amazon Athena JDBC driver as
     Tableau's Amazon Athena help page describes (driver in
     `C:\Program Files\Tableau\Drivers`, restart Tableau).
   - Get an access key: the account owner runs, in their own terminal,
     `aws iam create-access-key --user-name silvercare-tableau-1` and hands the key to
     the user privately. Keep it in a password manager or Tableau's saved credentials
     only; never in the repo, `secrets/`, chat, screenshots, or the report.
   - Connect: Server `athena.ap-southeast-1.amazonaws.com:443;Workgroup=silvercare-tableau`,
     Port 443, S3 staging directory
     `s3://silvercare-athena-results-<account_id>/tableau/`, the access key ID and
     secret. If only the default catalog appears, add
     `MetadataRetrievalMethod=ProxyAPI` to `athena.properties` in
     `My Tableau Repository\Datasources\` (Tableau's help page).
   - Pick `silvercare_lake.daily_summaries` and choose **Live** (never Extract).
   - Workbooks (`.twb`) may be saved; packaged workbooks (`.twbx`) and extracts may not,
     since they can contain data. Screenshots for the report are fine.
   - Go-live checks (criterion 11).
   - At the project's end (end of November 2026), the account owner deactivates and
     deletes every Tableau access key; then the users are removed from
     `local.tableau_users` in a PR.
   - If a key may have leaked: the owner deactivates it at once
     (`aws iam update-access-key --status Inactive ...`), creates a new one, then deletes
     the old one.
10. `docs/data-model.md`, S3 clean layer: the access table gains a row for the Tableau
    users (Athena: `daily_summaries` only in `silvercare-tableau`; direct S3: results
    prefix only). Retention gains nothing (results already expire at 7 days). "Before
    any real resident data arrives" gains: "decide whether Tableau users' laptops may
    display real resident data (docs/features/tableau-access.md)".

### Go-live checks (owner, after merge; listed in `docs/tableau.md`)

11. a. `aws lakeformation get-data-lake-settings --region ap-southeast-1` shows
       `CreateDatabaseDefaultPermissions` and `CreateTableDefaultPermissions` empty
       (otherwise `IAMAllowedPrincipals` makes every table visible, and the
       "daily_summaries only" rule doesn't hold; clean-data.md's owner step 6).
    b. In Tableau, connected as `silvercare-tableau-1`, `silvercare_lake` lists only
       `daily_summaries`, and a live sheet on it loads.
    c. A custom SQL query on `silvercare_lake.vitals` is refused (Lake Formation
       denies it).
    d. Removing `;Workgroup=silvercare-tableau` from the server field makes queries fail
       (the user may only use its own workgroup).

### Checks

12. `make tf-check` passes (fmt, validate, tflint, checkov). Any checkov skip has a
    reason in a comment (e.g. a group policy instead of a managed policy is intended).

## Data

- Reads: the Glue catalog entry and S3 data of `daily_summaries`, through Athena and
  Lake Formation (credential vending).
- Writes: Athena query results under `tableau/` in the results bucket.
- New access patterns or tables: none. No DynamoDB access, so no RCU or WCU use.

## IoT

None.

## API contract

None.

## Screens

None in the portal. The dashboards are built by hand in Tableau Desktop (out of scope).

## Alerts and notifications

None.

## Cost

Free or near free: IAM users and groups, Lake Formation, Glue reads within free
allowances, and the workgroup itself cost nothing. Each Tableau refresh runs Athena
queries at US$5/TB scanned with a 10 MB minimum per query (about US$0.00005 each, from
memory); a thousand refreshes cost about US$0.05. Results storage is tiny and expires
after 7 days. No fixed fees.

## Privacy

- Personal data touched: per-resident daily health statistics (opaque `resident_id`,
  no names), shown in Tableau Desktop on the Track D owner's laptop.
- Leaving `ap-southeast-1`: what Tableau displays, its on-disk query cache, and any
  values a `.twb` stores (live connection only; no extracts, no packaged workbooks, no
  publishing). Acceptable while all data is synthetic;
  before any real resident data arrives, revisit whether laptops may display it
  (add to "Before any real resident data arrives" in `docs/data-model.md`).
- Retention: on the laptop, only Tableau's query cache and any values a `.twb` stores
  (workbooks deleted and the cache cleared at project end, `docs/tableau.md`); Athena
  results for this workgroup expire after 7 days; Athena keeps query text for 45 days
  (AWS's fixed limit).
- Erasure: the record stays in `summaries/` (curated-layer.md); on the laptop, delete
  the workbooks and clear Tableau's cache.
- Credentials: the access key is the only secret; it never enters the repo, Terraform
  state, CI, or chat (criterion 9).
- Must not be logged or published: query results; screenshots in the report show
  synthetic data only.

## Out of scope

- Building the Tableau dashboards (done by hand in Tableau Desktop).
- Tableau Cloud, Tableau Server, Tableau Public, extracts, and packaged workbooks.
- Access to the clean tables, raw telemetry, or `rejected`.
- More Tableau users (added later with a one-line change to `local.tableau_users`).
- Portal changes.

## Open questions

None.
