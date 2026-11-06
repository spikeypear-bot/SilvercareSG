---
status: built          # draft | ready | built
---

# Seed data

## Summary

A developer script, `scripts/seed.py`, that fills `silvercare-registry` with 20
synthetic residents (profile and thresholds), assigns the 40 devices to them, creates 3
test Cognito users (doctor, nurse, caregiver), and links the caregiver user to
residents. It gives the alerting and API tracks known data to build and test against,
and gives the simulator its residents. It writes to the live account (run by a developer
on their own machine) or to DynamoDB Local (`--local`, for integration tests). All data
is obviously synthetic.

## Users and access

No Cognito group uses this feature. There is no API and no screen. The users it creates
then see the existing frontend according to their group.

| Group | Can see | Can do |
|---|---|---|
| staff | nothing (new) | nothing (new) |
| caregiver | nothing (new) | nothing (new) |

The `family` group is removed (team decision 2026-11-04, docs/features/alert-routing.md:
family members get caregiver accounts), so the seed has no family user (change of
2026-11-04, criterion 15a).

How access to a specific resident is decided: not changed. The seed writes the
user ↔ resident link items that the API's link check (pattern 13) reads later.

Who does what:

| Who | Does |
|---|---|
| Developer (own machine, shared developer IAM user) | Runs `python scripts/seed.py` against the live account; shares `secrets/test-users.txt` privately |
| Developer or CI (dev container) | Runs `make seed-local` / integration tests against DynamoDB Local |
| Claude and its agents | Build and test the script. They have no AWS credentials, never run it against AWS, and never read `secrets/` |

The developer IAM user already has every permission the script needs
(`docs/iam/developer-policy.json`: `ReadAndEditTableItems`, `ManageCognitoUsers`,
`cognito-idp:List*`). No IAM or Terraform change.

## Acceptance criteria

### Seed file

1. All seed values live in one committed file, `scripts/seed-data.json`, read by
   `scripts/seed.py` (and later by the simulator). It holds the 20 residents, their
   thresholds, the device assignments, the 3 test users (without passwords), and the
   links. Nothing in it is real personal data.
2. Residents are `res-0001` … `res-0020`. Resident `res-000N` lives in `home-000N` and is
   assigned `wearable-000N` and `bedroom-000N` (N = 1…20, zero-padded to 4 digits). All
   40 devices in `infra/devices/devices.json` are assigned; there are no spares.
3. Given a seed file whose devices don't exactly match `infra/devices/devices.json`
   (missing, extra, or wrong type), a duplicate resident ID, a link to a resident or user
   that doesn't exist, or a `password` field anywhere, the script exits non-zero before
   creating any AWS client or writing anything, naming each problem by ID.

### Registry items (shapes from `docs/data-model.md`)

4. One **resident profile** per resident: `pk = RESIDENT#res-000N`, `sk = PROFILE`,
   `entity_type = RESIDENT`, `resident_id`, `display_name` `"Test Resident A"` …
   `"Test Resident T"` (A = res-0001), `home_id`, `year_of_birth` (an integer from
   1935 to 1955), `medical_notes` starting with `"Synthetic note: "` (max 2,000
   characters), `emergency_contacts` with exactly 1 contact
   `{"name": "Test Contact <letter>1", "relationship": <e.g. "daughter">, "phone": "+65 0000 00NN"}`
   (NN = resident number), `created_at`, `updated_at`. No `photo_key`.
5. One **thresholds** item per resident: `pk = THRESHOLDS#res-000N`, `sk = THRESHOLDS`,
   `hr_bpm {min, max}`, `spo2_pct {min}`, `skin_temp_c {min, max}`, `updated_at`,
   `updated_by = "seed"`. Defaults: `hr_bpm` 45–130, `spo2_pct` min 88, `skin_temp_c`
   30.0–38.0. Exceptions:

   | Resident | Change | Synthetic reason (in `medical_notes`) |
   |---|---|---|
   | `res-0004` | `spo2_pct` min 85 | low normal SpO2 |
   | `res-0007` | `hr_bpm` max 140 | high resting heart rate |
   | `res-0011` | `hr_bpm` min 40 | low resting heart rate |

   The HR and SpO2 defaults are the wearable's instant-alert limits (`docs/metrics.md`);
   the skin temperature range is a placeholder. The alerting feature (Track A) may change
   any of these values. Numbers are written as DynamoDB numbers (`N`), not strings.
6. One **device assignment** item per device: `pk = DEVICE#<device_id>`,
   `sk = ASSIGNMENT`, `entity_type = DEVICE`, `device_type`, `resident_id`, `home_id`,
   `assigned_at`, `gsi1_pk = RESIDENT#<resident_id>`, `gsi1_sk = DEVICE#<device_id>`.
7. **User ↔ resident links**: `pk = USER#<cognito sub>`, `sk = RESIDENT#<resident_id>`,
   `relationship` `caregiver`, `linked_at`, `linked_by = "seed"`,
   `gsi1_pk = RESIDENT#<resident_id>`, `gsi1_sk = USER#<cognito sub>`:
   - test caregiver → `res-0001`, `res-0002`, `res-0003`

   The `sub` is read from Cognito after the user exists (step order: users, then links).
8. After a full seed: pattern 4 (`by-type`, `entity_type = DEVICE`) returns 40 items;
   pattern 8 (`entity_type = RESIDENT`) returns 20; pattern 10 for `res-0002` returns
   `wearable-0002` and `bedroom-0002`; pattern 14 for the caregiver user returns
   `res-0001`, `res-0002`, and `res-0003`; pattern 15 for `res-0002` returns the test
   caregiver.

### Cognito test users (live account only)

9. The script finds the user pool by name (`silvercare-users`, via `ListUserPools`,
   following pagination), or uses `--user-pool-id` (must start with `ap-southeast-1_`).
   Given no pool or more than one with that name, it exits non-zero before writing
   anything.
10. It creates 3 users, each in exactly one group:

    | Email (username) | `name` | Group |
    |---|---|---|
    | `test-doctor@example.com` | Test Doctor | `doctor` |
    | `test-nurse@example.com` | Test Nurse | `nurse` |
    | `test-caregiver@example.com` | Test Caregiver | `caregiver` |

    Created with `AdminCreateUser` (`MessageAction = SUPPRESS`, so no email is sent;
    `email_verified = true`), then `AdminSetUserPassword` with `Permanent = true`, then
    `AdminAddUserToGroup`. Users not in this table (e.g. accounts teammates made by hand)
    are never read, changed, or deleted.
11. Each new user's password is random, generated with Python's `secrets` module,
    20 characters, and meets the pool policy (at least one lowercase, uppercase, number,
    and symbol). The script writes `email`, group, and password for each user it set a
    password for to `secrets/test-users.txt` (override: `--passwords-file`), and prints
    only the file path and a count. Passwords never appear on stdout, in logs, or in
    exceptions.
12. Given a user that already exists, the script keeps it and its password, makes sure
    it is in its group (removing it from the other two of the three groups; other
    groups are left alone), and doesn't change its line in the passwords file unless
    `--reset-passwords` is given. With `--reset-passwords`, all 3 users get new
    passwords. An existing user's `name` and email attributes are not changed.
13. The passwords file is **merged, never truncated**: lines for users whose password
    wasn't set this run are kept. It is written atomically (temporary file in the same
    folder created with mode `600`, then `os.replace`), so a failed write leaves the old
    file intact. The folder is created with mode `700` if missing. Given a
    `--passwords-file` inside the repository but outside `secrets/`, the script exits
    non-zero before writing anything (paths outside the repository are allowed, e.g. for
    tests).
14. Given `--local`, the script skips Cognito and STS entirely and uses fixed synthetic
    subs from the seed file (`00000000-0000-4000-8000-00000000000N`, N = 1…3) for the
    link items.

### Re-runs and delete

15. Re-running the seed resets every seeded registry item to the seed values
    (`PutItem` overwrite). The result after two runs is identical to one run, except
    timestamps. Registry items the seed doesn't own (other `RESIDENT#`, `THRESHOLDS#`,
    `DEVICE#`, `USER#` items, and links with `linked_by` other than `seed`) are never
    touched. Device IDs are real hardware: given a seeded device whose existing
    assignment has a `resident_id` outside the seed, the seed exits non-zero before
    writing anything (device IDs only in the message), and `--delete` keeps that item.
    Unassigned devices (no `resident_id`, e.g. spares from `scripts/seed-registry.py`)
    hold no resident's data and are assigned.
15a. **Clean-up of what the seed no longer has** (2026-11-04, family removed). On every
    run (live and `--local`), after writing the links, the script deletes every link to
    a seeded resident with `linked_by = "seed"` (found as `--delete` finds them) whose
    key is not one of the links it just wrote, and prints `registry: N old seed links
    removed` (N may be 0). Links with any other `linked_by` are never touched. On the
    live account it also deletes the **retired** test user `test-family@example.com`
    (that exact username only, a user the seed itself created before) if it exists and
    is still as the seed left it (`name` `Test Family`, in no group other than
    `family`), prints `cognito: test-family deleted (retired)` or nothing if it is
    absent, and removes its line from the passwords file (same merge and atomic-write
    rules as criterion 13). If someone changed it by hand (e.g. made it a caregiver),
    it is kept with `cognito: test-family kept (changed by hand)` and its password line
    is kept too. Each old-link delete is conditional on `linked_by` still being
    `seed`. `--delete` does the same for the retired user. No other user is ever
    deleted outside `--delete`, and the other test users keep their passwords.
16. `--delete` removes everything the seed owns: the 20 profiles, 20 thresholds items,
    40 device assignments, every link to a seeded resident with `linked_by = "seed"`
    (found through `by-resident`, pattern 15, then read to check `linked_by`; **not**
    through the users' current Cognito subs, so links of a test user already deleted by
    hand are removed too), and (live account only) the 3 test users and their lines in
    the passwords file (the file is deleted if no user lines are left; it is left alone
    if missing or if no lines match). Given some of them are already gone, it still
    succeeds. It does not touch `silvercare-latest`, `silvercare-alerts`, or S3.
17. Flag combinations that make no sense exit non-zero with a usage error:
    `--local` with `--user-pool-id` or `--reset-passwords`; `--delete` with
    `--reset-passwords`.

### Targets and safety

18. Default target is the live account: region `ap-southeast-1` (fixed in the client
    config, whatever the environment says), credentials from the developer's normal AWS
    profile. Before writing, the script prints the account ID (from
    `sts:GetCallerIdentity`), the region, the pool ID, and what it will do, and asks the
    developer to type `seed` (or `delete`) to continue; `--yes` skips the prompt. Given
    any other input or end of input, it prints that nothing was written and exits
    non-zero.
19. `--local` writes to DynamoDB Local at `DYNAMODB_ENDPOINT` (default
    `http://dynamodb-local:8000`) with dummy credentials. If `silvercare-registry`
    doesn't exist there, the script creates it with the same keys, indexes, projections,
    and capacity as `infra/dynamodb.tf`. No confirmation prompt, no STS call.
20. `make seed-local` runs `python scripts/seed.py --local` in the dev container.
    The seed loader and item builders are importable functions (including a
    `seed_local(client, table=...)` helper), so integration tests for the API and
    alerting can seed their own DynamoDB Local tables.
21. Writes are paced for the registry's 1 WCU: boto3 `adaptive` retry mode, and
    `BatchWriteItem` (at most 25 per call) with unprocessed items retried with jittered
    exponential backoff; it gives up with an error only after repeated retries with no
    progress. A full live seed finishes in under 5 minutes and doesn't fail because of
    throttling.
22. Output: one line per step with counts and IDs only (e.g.
    `registry: 83 items written`, `cognito: test-nurse created`). Never item contents,
    names, or passwords; AWS errors are shown as operation name and error code only.
    Exit 0 only if every step succeeded; 1 for an AWS failure or an aborted prompt; 2 for
    usage or validation errors.

### Tests

23. Unit tests in `scripts/tests/` (moto for DynamoDB, Cognito, and STS) cover criteria
    3–19 and 22, including: validation failures write nothing; a re-run is idempotent;
    an existing user's password isn't changed without `--reset-passwords`; no password
    is printed; the passwords file merge, atomic write, mode, and path rule; `--delete`
    on a partly deleted seed and after a test user was deleted by hand succeeds and
    leaves no seed links; a re-run after a link was removed from the seed file deletes
    that link and keeps every non-seed link; the retired user is deleted (and only it,
    a hand-made user in the `family` group is untouched); the confirmation prompt
    refuses anything but `seed` /
    `delete`; foreign items and hand-made users are untouched.
24. An integration test (`@pytest.mark.integration`) runs `--local` against DynamoDB
    Local twice and checks criterion 8's query results. It skips when
    `DYNAMODB_ENDPOINT` is unset.

## Data

- Reads: `infra/devices/devices.json`, `scripts/seed-data.json`; Cognito
  `ListUserPools`, `AdminGetUser` (for the `sub`); registry `Query` on `by-resident`
  and `BatchGetItem` (for `--delete` only).
- Writes: registry items listed above via `BatchWriteItem` (access pattern 17).
  Overwrites are intended (criterion 15), so duplicates are not an issue.
- About 83 items, about 190 WCU including index writes.
- New access patterns or tables: none.

## IoT

None. Device IDs come from `infra/devices/devices.json`; nothing is published.

## API contract

None.

## Screens

None. Seeded users sign in to the existing frontend, which still shows demo data until
the API track replaces it.

## Alerts and notifications

None. Thresholds are written, but nothing checks them yet.

## Privacy

- Personal data touched: synthetic only. Names `Test Resident <letter>`,
  `Test Contact <letter>1`, `Test <Role>`; `example.com` emails (reserved, can't
  receive mail); `+65 0000 00NN` phone numbers; notes prefixed `Synthetic note:`.
- Passwords exist only in Cognito and in the git-ignored `secrets/test-users.txt`.
- Reserved IDs: `res-0001`…`res-0020`, `home-0001`…`home-0020`, and the
  `00000000-0000-4000-8000-…` local subs are for synthetic seed data only and are never
  given to real residents, homes, or users. Record this in `docs/data-model.md`, with a
  note that seeded items have `updated_by` / `linked_by` = `seed`.
- Retention: until `--delete`. Before real resident data arrives, the seed must be
  deleted (`--delete`), since the test passwords are shared among the team.
- Must not be logged or shown: passwords; item contents (keep the habit for when data is
  real).

## Out of scope

- Resident photos.
- The simulator and per-resident vitals baselines (the `simulator` feature; it reads
  `scripts/seed-data.json` and may add baseline fields to it).
- Alert items, `silvercare-latest` items, S3 history.
- An admin screen or API for linking users to residents (Track B).
- Real email delivery for test users.

## Open questions

- None.
