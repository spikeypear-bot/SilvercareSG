# Always-on simulator on EC2 (docs/features/simulator-ec2.md). An agreed exception to
# "serverless only" (user, 2026-11-09), removed by a PR on 2026-11-30.
# Cost: t4g.micro US$0.0106/h + public IPv4 US$0.005/h + 8 GB gp3 ≈ US$12/month.
#
# The 40 device keys are SSM parameters /silvercare/device-keys/<id> written by
# scripts/upload-device-keys.py, NOT Terraform resources: aws_ssm_parameter would
# read the private keys back into state.

locals {
  simulator_keys_path = "/silvercare/device-keys"
  simulator_keys_arn  = "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${local.simulator_keys_path}/*"
}

# AWS's public parameter for the latest Amazon Linux 2023 arm64 AMI (not ours).
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "simulator" {
  name        = "silvercare-simulator"
  description = "Simulator instance: no inbound, outbound HTTPS and MQTT only"
  vpc_id      = data.aws_vpc.default.id

  #checkov:skip=CKV_AWS_382:Egress must reach AWS IoT, SSM, GitHub, dnf and PyPI on public IPs; limited to ports 443 and 8883
  egress {
    description = "HTTPS: SSM, Parameter Store, GitHub, dnf, PyPI"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "MQTT over TLS to AWS IoT Core"
    from_port   = 8883
    to_port     = 8883
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_iam_policy_document" "simulator_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "simulator" {
  name               = "silvercare-simulator"
  assume_role_policy = data.aws_iam_policy_document.simulator_assume.json
}

data "aws_iam_policy_document" "simulator" {
  #checkov:skip=CKV_AWS_356:Session Manager's documented minimum uses "*"; the key read is scoped to its path
  #checkov:skip=CKV_AWS_111:Only SSM agent registration and session channels are unscoped (AWS documents "*")
  statement {
    sid       = "ReadDeviceKeys"
    actions   = ["ssm:GetParameter"]
    resources = [local.simulator_keys_arn]
  }

  # Session Manager's documented minimum ("Create a custom IAM role for Session
  # Manager"). AWS supports only "*" for these actions.
  statement {
    sid = "SessionManager"
    actions = [
      "ssm:UpdateInstanceInformation",
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "simulator" {
  name   = "silvercare-simulator"
  role   = aws_iam_role.simulator.id
  policy = data.aws_iam_policy_document.simulator.json
}

resource "aws_iam_instance_profile" "simulator" {
  name = "silvercare-simulator"
  role = aws_iam_role.simulator.name
}

resource "aws_instance" "simulator" {
  #checkov:skip=CKV_AWS_88:Public IPv4 needed for outbound internet without a NAT gateway; no inbound rules
  #checkov:skip=CKV_AWS_126:Detailed monitoring costs extra and nothing reads it
  ami                         = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type               = "t4g.micro"
  iam_instance_profile        = aws_iam_instance_profile.simulator.name
  vpc_security_group_ids      = [aws_security_group.simulator.id]
  associate_public_ip_address = true
  ebs_optimized               = true
  monitoring                  = false

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }

  user_data = templatefile("${path.module}/simulator/user-data.sh.tftpl", {
    iot_endpoint = data.aws_iot_endpoint.data.endpoint_address
    repo_url     = "https://github.com/spikeypear-bot/SilvercareSG.git"
  })
  user_data_replace_on_change = false

  tags = {
    Name = "silvercare-simulator"
    role = "simulator" # the developer policy allows sessions only on this tag
  }

  lifecycle {
    # A new AMI or edited boot script must never replace or restart the running
    # simulator on deploy: user data only runs on first boot anyway.
    ignore_changes = [ami, user_data]
  }
}

output "simulator_instance_id" {
  description = "Simulator EC2 instance ID, for aws ssm start-session --target."
  value       = aws_instance.simulator.id
}
