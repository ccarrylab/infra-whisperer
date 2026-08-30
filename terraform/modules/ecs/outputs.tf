output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "service_name" {
  value = aws_ecs_service.this.name
}

output "service_security_group_id" {
  value = aws_security_group.service.id
}

output "execution_role_arn" {
  value = aws_iam_role.execution.arn
}