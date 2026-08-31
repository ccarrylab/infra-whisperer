output "alb_dns_name" {
  description = "Public URL of the demo app"
  value       = module.alb.alb_dns_name
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "ecs_service_name" {
  value = module.ecs.service_name
}

output "rds_endpoint" {
  value     = module.rds.db_endpoint
  sensitive = true
}

output "vpc_id" {
  value = local.vpc_id
}

output "agent_diagnosis_role_arn" {
  description = "Set as AGENT_DIAGNOSIS_ROLE_ARN"
  value       = module.safety.agent_diagnosis_role_arn
}
output "agent_plan_role_arn" {
  description = "Set as AGENT_PLAN_ROLE_ARN GitHub Actions variable"
  value       = module.safety.agent_plan_role_arn
}
output "agent_apply_role_arn" {
  description = "Set as AGENT_APPLY_ROLE_ARN GitHub Actions variable"
  value       = module.safety.agent_apply_role_arn
}
output "tf_state_bucket" {
  description = "Set as TF_STATE_BUCKET GitHub Actions variable"
  value       = module.safety.s3_bucket_name
}
output "tf_lock_table" {
  description = "Set as TF_LOCK_TABLE GitHub Actions variable"
  value       = module.safety.dynamodb_table_name
}
