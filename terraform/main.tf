data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  use_existing_network = var.existing_vpc_id != null

  vpc_id             = local.use_existing_network ? var.existing_vpc_id : module.vpc[0].vpc_id
  public_subnet_ids  = local.use_existing_network ? var.existing_public_subnet_ids : module.vpc[0].public_subnet_ids
  private_subnet_ids = local.use_existing_network ? var.existing_private_subnet_ids : module.vpc[0].private_subnet_ids
}

# Only created when existing_vpc_id is not set. Terraform 1.5+ supports
# count on modules, which is what makes this conditional-creation pattern
# possible.
module "vpc" {
  count  = local.use_existing_network ? 0 : 1
  source = "./modules/vpc"

  project_name = var.project_name
  vpc_cidr     = var.vpc_cidr
  azs          = local.azs
}

module "alb" {
  source = "./modules/alb"

  project_name   = var.project_name
  vpc_id         = local.vpc_id
  public_subnets = local.public_subnet_ids
  container_port = var.container_port
}

module "rds" {
  source = "./modules/rds"

  project_name      = var.project_name
  vpc_id            = local.vpc_id
  private_subnets   = local.private_subnet_ids
  db_name           = var.db_name
  db_username       = var.db_username
  db_password       = var.db_password
  db_instance_class = var.db_instance_class
  # Deliberately open only to the ECS service security group — this is the
  # kind of rule the chaos scenarios mutate to simulate a real incident.
  allowed_sg_id = module.ecs.service_security_group_id
}

module "ecs" {
  source = "./modules/ecs"

  project_name          = var.project_name
  vpc_id                = local.vpc_id
  private_subnets       = local.private_subnet_ids
  container_image       = var.container_image
  container_port        = var.container_port
  desired_count         = var.ecs_desired_count
  alb_target_group_arn  = module.alb.target_group_arn
  alb_security_group_id = module.alb.alb_security_group_id
  assign_public_ip      = var.ecs_assign_public_ip
}

module "monitoring" {
  source = "./modules/monitoring"

  project_name             = var.project_name
  ecs_cluster_name         = module.ecs.cluster_name
  ecs_service_name         = module.ecs.service_name
  alb_arn_suffix           = module.alb.alb_arn_suffix
  target_group_arn_suffix  = module.alb.target_group_arn_suffix
  db_instance_id           = module.rds.db_instance_id
  alarm_notification_email = var.alarm_sns_email
}

module "budget" {
  source = "./modules/budget"

  project_name       = var.project_name
  budget_limit_usd   = var.budget_limit_usd
  budget_alert_email = var.budget_alert_email
}
