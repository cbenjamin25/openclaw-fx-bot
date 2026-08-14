terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

# ── Networking: egress-only, no inbound. Admin access via SSM Session
# Manager, not SSH — no open ports, no key management.
resource "aws_security_group" "fxbot" {
  name_prefix = "fxbot-"
  description = "fxbot egress only"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS to Oanda, AWS APIs, Anthropic"
  }
}

# ── IAM: least privilege. Read /fxbot/* params, write logs/metrics,
# publish to the alert topic, and be reachable via Session Manager.
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fxbot" {
  name_prefix        = "fxbot-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "fxbot" {
  statement {
    sid       = "ReadSecrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:*:parameter/fxbot/*"]
  }
  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.fxbot.arn}:*"]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["FXBot"]
    }
  }
  statement {
    sid       = "Alerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_role_policy" "fxbot" {
  role   = aws_iam_role.fxbot.id
  policy = data.aws_iam_policy_document.fxbot.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.fxbot.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "fxbot" {
  name_prefix = "fxbot-"
  role        = aws_iam_role.fxbot.name
}

# ── Observability
resource "aws_cloudwatch_log_group" "fxbot" {
  name              = "/fxbot/app"
  retention_in_days = 90
}

resource "aws_sns_topic" "alerts" {
  name = "fxbot-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Heartbeat alarm: the bot publishes FXBot/Heartbeat every 30s.
# Missing data for 2 minutes = ALARM = page Cedric. (The in-app
# dead-man watchdog that flattens positions arrives in Phase 3;
# this alarm is the independent second layer.)
resource "aws_cloudwatch_metric_alarm" "heartbeat" {
  alarm_name          = "fxbot-heartbeat-stale"
  namespace           = "FXBot"
  metric_name         = "Heartbeat"
  statistic           = "SampleCount"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# ── Compute
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_instance" "fxbot" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.fxbot.id]
  iam_instance_profile   = aws_iam_instance_profile.fxbot.name

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail
    # Lesson 2026-08-14: egress is 443-only, but Ubuntu's default apt
    # mirrors use plain HTTP (port 80). Switch to HTTPS mirrors first
    # or every install below fails with "Network is unreachable".
    sed -i 's|http://us-east-1.ec2.archive.ubuntu.com|https://archive.ubuntu.com|g; s|http://security.ubuntu.com|https://security.ubuntu.com|g' /etc/apt/sources.list.d/ubuntu.sources
    apt-get update
    apt-get install -y python3.12-venv git unzip
    # Lesson 2026-08-14: amazon-cloudwatch-agent is NOT in Ubuntu's apt
    # repos; install Amazon's official .deb over HTTPS instead.
    curl -sLo /tmp/cwagent.deb "https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb"
    dpkg -i /tmp/cwagent.deb
    # AWS CLI v2 (not preinstalled on Ubuntu AMIs; needed for SSM reads)
    curl -sLo /tmp/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
    unzip -q /tmp/awscliv2.zip -d /tmp
    /tmp/aws/install
    useradd -m -s /bin/bash fxbot || true
  EOF

  tags = {
    Name    = "fxbot"
    Project = "openclaw-fx-bot"
  }
}
