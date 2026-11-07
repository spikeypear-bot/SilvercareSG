# Tableau setup

How to connect Tableau Desktop to SilverCare's curated data, for the Track D analytics
dashboards. Spec: `docs/features/tableau-access.md`.

What you get: read-only, **live** access to one table, `silvercare_lake.daily_summaries`
(one row per resident per Singapore day, docs/features/curated-layer.md), through
Amazon Athena in the `silvercare-tableau` workgroup. Nothing else in the account is
reachable with this user.

## Rules

- **Live connection only.** Never create an extract (`.hyper`) or save a packaged
  workbook (`.twbx`): both store the data on your laptop. Plain workbooks (`.twb`) are
  fine, but know that they can still hold some values (filter lists, aliases, groups),
  and Tableau keeps a query cache on disk. That's acceptable while all data is
  synthetic.
- **Never publish.** Don't sign in to or publish to Tableau Public, Tableau Cloud, or
  Tableau Server with this connection. Workbooks stay as local `.twb` files on your
  laptop.
- **The access key is a secret.** Keep it in a password manager or in Tableau's saved
  credentials only. Never put it in the repo, `secrets/`, a chat (including Claude), an
  email, a screenshot, or the report.
- **Screenshots** for the report and slides are fine: all data is synthetic.
- Users are named `silvercare-tableau-1`, `-2`, ... The repo is public, so who holds
  which user is agreed in the team, not written here.

## 1. Get an access key (account owner)

The account owner runs this in their own terminal, with credentials for the project
account (`753674274065`), then hands the two values to the user privately:

```sh
aws iam create-access-key --user-name silvercare-tableau-1
```

It prints an `AccessKeyId` and a `SecretAccessKey`. The secret is shown only once; if
it's lost, create a new key and delete the old one (section 6).

## 2. Install Tableau Desktop and the Athena driver

1. Install **Tableau Desktop** (free edition).
2. Install the **Amazon Athena JDBC driver** as Tableau's help page describes
   (<https://help.tableau.com/current/pro/desktop/en-us/examples_amazonathena.htm>):
   download the driver it links to and copy the `.jar` file to
   `C:\Program Files\Tableau\Drivers` (Mac: `~/Library/Tableau/Drivers`).
3. Restart Tableau.

## 3. Connect

In Tableau: **Connect → To a Server → Amazon Athena**, then enter:

| Field | Value |
|---|---|
| Server | `athena.ap-southeast-1.amazonaws.com:443;Workgroup=silvercare-tableau` |
| Port | `443` |
| S3 Staging Directory | `s3://silvercare-athena-results-753674274065/tableau/` |
| Access Key ID | from step 1 |
| Secret Access Key | from step 1 |

`;Workgroup=silvercare-tableau` must stay in the server field: the user may only run
queries in that workgroup, and without it Athena uses another workgroup and refuses.

Then:

1. Pick the catalog `AwsDataCatalog`, the database `silvercare_lake`, and drag
   `daily_summaries` onto the canvas. It should be the only table you can see.
2. At the top right of the data source page, choose **Live** (not Extract).

If only the default catalog appears, close Tableau, create the file
`My Tableau Repository\Datasources\athena.properties` (in your Documents folder) with
this line, and reopen Tableau:

```
MetadataRetrievalMethod=ProxyAPI
```

## 4. Working with the data

- Columns are listed in docs/features/curated-layer.md (criterion 11): `resident_id`,
  `date` (the Singapore date, also the partition), heart rate, SpO2 and skin
  temperature min / average / max, `activity_pct_avg`, `vitals_readings`, the
  `*_below` / `*_above` counts against the resident's thresholds, `thresholds`,
  `bed_minutes`, `motion_events`, `falls`, `vitals_alert_episodes`, `cancels`.
- Filter on `date` where you can: Athena then reads only those days.
- Each query has a 1 GB scan limit; at this project's size a query reads a few KB.
- There are no names: residents are opaque IDs like `res-0003`.

## 5. Go-live checks (after the feature is merged and deployed)

Run these once, in order. Steps a and the key creation need the account owner.

a. **No account-wide default grants** (otherwise every table would be visible):

```sh
aws lakeformation get-data-lake-settings --region ap-southeast-1
```

✅ `CreateDatabaseDefaultPermissions` and `CreateTableDefaultPermissions` are both
empty (`[]`). If either lists `IAM_ALLOWED_PRINCIPALS`, stop and tell the team.

b. **Only `daily_summaries` is visible and loads:** connect as in section 3. ✅
`silvercare_lake` lists only `daily_summaries`, and a sheet with, for example, the
average `hr_bpm_avg` per `date` loads.

c. **Other tables are refused:** in the data source page choose **New Custom SQL** and
run `SELECT * FROM silvercare_lake.vitals LIMIT 1`. ✅ It fails with a permissions
error.

d. **Only its own workgroup:** edit the connection, remove
`;Workgroup=silvercare-tableau` from the server field, and refresh. ✅ It fails. Put it
back afterwards.

If a check doesn't pass, send the error message (not the key) to the team.

## 6. If a key may have leaked

The account owner, straight away:

```sh
aws iam update-access-key --user-name silvercare-tableau-1 --access-key-id <old key id> --status Inactive
aws iam create-access-key --user-name silvercare-tableau-1
aws iam delete-access-key --user-name silvercare-tableau-1 --access-key-id <old key id>
```

Hand over the new key privately and update Tableau's saved credentials.

## 7. Adding a teammate

1. In `infra/tableau.tf`, add the next neutral name (e.g. `silvercare-tableau-2`) to
   `local.tableau_users`, open a PR, and check the plan comment: it should only add that
   user, its group membership, and its Lake Formation grants.
2. After the merge, the account owner creates its key (section 1).

## 8. At the end of the project (end of November 2026)

1. The account owner deactivates and deletes every Tableau user's access keys:

   ```sh
   aws iam list-access-keys --user-name silvercare-tableau-1
   aws iam delete-access-key --user-name silvercare-tableau-1 --access-key-id <key id>
   ```

2. Then remove the users from `local.tableau_users` in a PR (Terraform can't delete a
   user that still has a key).
3. Each Tableau user deletes their SilverCare workbooks (`.twb`) and any screenshots
   not used in the report, and clears Tableau's cache (quit Tableau, then delete the
   cache folder Tableau's help names for your version).
