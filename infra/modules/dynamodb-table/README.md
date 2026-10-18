# dynamodb-table

One DynamoDB table with the SilverCare defaults from `docs/data-model.md` built in, so
every table gets them without the caller opting in:

- `billing_mode = "PROVISIONED"` with fixed read/write capacity (no auto scaling), so
  all tables together can stay inside the always-free 25 RCU / 25 WCU
- point-in-time recovery on
- deletion protection on
- encryption at rest with the AWS owned key (free; no customer-managed KMS key)
- no stream
- string keys only; attribute definitions are derived from the table and index keys
- indexes project only the listed attributes (`INCLUDE`), or only the keys if none are
  listed; `ALL` is not offered

Changing `name`, `hash_key`, or `range_key` **destroys the table**. Deletion protection
makes the apply fail instead, but don't rely on that: agree a migration plan first.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name` | `string` | required | Table name |
| `hash_key` | `string` | required | Partition key attribute |
| `range_key` | `string` | required | Sort key attribute |
| `read_capacity` | `number` | required | Table RCU |
| `write_capacity` | `number` | required | Table WCU |
| `global_secondary_indexes` | `list(object)` | `[]` | `name`, `hash_key`, `range_key`, `non_key_attributes`, `read_capacity`, `write_capacity` |
| `ttl_attribute` | `string` | `null` | TTL attribute (epoch seconds), or `null` for no TTL |
| `tags` | `map(string)` | `{}` | Extra tags (provider `default_tags` are added anyway) |

## Outputs

| Name | Description |
|---|---|
| `name` | Table name |
| `arn` | Table ARN. Index ARNs are `<arn>/index/<index-name>` |

## Example

```hcl
module "alerts_table" {
  source = "./modules/dynamodb-table"

  name           = "silvercare-alerts"
  hash_key       = "pk"
  range_key      = "sk"
  read_capacity  = 3
  write_capacity = 2
  ttl_attribute  = "expires_at"

  global_secondary_indexes = [
    {
      name               = "open"
      hash_key           = "open_pk"
      range_key          = "sk"
      non_key_attributes = ["alert_type", "device_id", "status", "event_ts"]
      read_capacity      = 2
      write_capacity     = 1
    },
  ]
}
```
