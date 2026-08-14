output "instance_id" {
  value = aws_instance.fxbot.id
}

output "connect_command" {
  value = "aws ssm start-session --target ${aws_instance.fxbot.id}"
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}
