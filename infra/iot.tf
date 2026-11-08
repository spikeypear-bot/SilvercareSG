# AWS IoT Core foundation (docs/features/iot-core.md): every device in
# devices/devices.json is a thing with its own certificate, created from the committed
# CSR in devices/csr/<id>.csr, and can publish only under its own topics.
#
# Nothing processes the messages yet (no topic rules), so IoT Core drops them.
# Things, thing types, groups, certificates, and policies are free.

locals {
  # The single device list, also read by scripts/device-keys.py.
  iot_devices = jsondecode(file("${path.module}/devices/devices.json")).devices
}

module "iot_devices" {
  source = "./modules/iot-devices"

  devices = local.iot_devices
  csr_dir = "${path.module}/devices/csr"
}

# The account's ATS data endpoint that devices connect to (MQTT over TLS, port 8883).
# Also the HTTPS endpoint the Lambdas use for device shadows (IOT_DATA_ENDPOINT).
data "aws_iot_endpoint" "data" {
  endpoint_type = "iot:Data-ATS"
}

# Every wearable thing: the only shadows the Lambdas may read or write
# (docs/features/device-alert-limits.md, criteria 5c, 12, 15). Never bedroom sensors.
locals {
  wearable_things_arn = "arn:aws:iot:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:thing/wearable-*"
}
