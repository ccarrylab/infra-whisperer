variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "infra-whisperer"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.42.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to span (only used when creating a new VPC)"
  type        = number
  default     = 2
}

variable "existing_vpc_id" {
  description = "ID of an existing VPC to use instead of creating a new one. Leave null to have Terraform create a fresh VPC. Set this to reuse infra you already have and save on NAT Gateway / EIP cost."
  type        = string
  default     = null
}

variable "existing_public_subnet_ids" {
  description = "Public subnet IDs in the existing VPC, for the ALB. Required if existing_vpc_id is set. Needs internet-gateway routing already in place."
  type        = list(string)
  default     = []
}

variable "ecs_assign_public_ip" {
  description = "Set true if the subnets you're passing in (existing or created) are actually public with no NAT tier — e.g. a default VPC. ECS tasks then get a public IP directly instead of relying on NAT for outbound internet access."
  type        = bool
  default     = false
}

variable "existing_private_subnet_ids" {
  description = "Private subnet IDs in the existing VPC, for ECS tasks and RDS. Required if existing_vpc_id is set. Needs NAT/route-table access already in place if your ECS tasks need outbound internet (e.g. to pull images)."
  type        = list(string)
  default     = []
}

variable "container_image" {
  description = "Container image for the demo app running on ECS"
  type        = string
  default     = "public.ecr.aws/docker/library/httpd:latest" # placeholder demo app
}

variable "container_port" {
  description = "Port the demo app listens on"
  type        = number
  default     = 80
}

variable "ecs_desired_count" {
  description = "Desired number of ECS tasks"
  type        = number
  default     = 2
}

variable "db_name" {
  description = "Name of the demo Postgres database"
  type        = string
  default     = "infrawhisperer"
}

variable "db_username" {
  description = "Master username for RDS"
  type        = string
  default     = "iwadmin"
}

variable "db_password" {
  description = "Master password for RDS (set via terraform.tfvars or TF_VAR_db_password env var — do not commit)"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance class — keep small to control cost"
  type        = string
  default     = "db.t4g.micro"
}

variable "budget_limit_usd" {
  description = "Hard monthly cost cap for this project. Non-optional — protects your AWS credit."
  type        = number
  default     = 25
}

variable "budget_alert_email" {
  description = "Email to notify when budget thresholds are hit"
  type        = string
}

variable "alarm_sns_email" {
  description = "Email to notify for CloudWatch alarms (feeds the agent's incident trigger)"
  type        = string
}
