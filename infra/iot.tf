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
data "aws_iot_endpoint" "data" {
  endpoint_type = "iot:Data-ATS"
}
