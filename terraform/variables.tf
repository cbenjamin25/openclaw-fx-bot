variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_id" {
  type        = string
  description = "VPC to deploy into (your default VPC is fine)"
}

variable "subnet_id" {
  type        = string
  description = "Public subnet with auto-assign public IP (egress via IGW; no inbound rules exist)"
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "alert_email" {
  type        = string
  description = "Email for SNS alerts (confirm the subscription email AWS sends)"
}
