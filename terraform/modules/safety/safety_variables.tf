variable "project_name" {
  description = "Project name, used for resource naming"
  type        = string
  default     = "infra-whisperer"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "trusted_principal_arn" {
  description = "ARN of the principal that can assume diagnosis/plan roles (EC2 instance role, ECS task role, or your IAM user)"
  type        = string
}

variable "github_org" {
  description = "GitHub organization name"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
}

variable "ecs_cluster_arn" {
  description = "ARN of the ECS cluster to scope permissions to"
  type        = string
}

variable "rds_instance_arn" {
  description = "ARN of the RDS instance to scope permissions to"
  type        = string
}

variable "ecs_execution_role_arn" {
  description = "ARN of the ECS execution role that the agent may need to modify IAM policies for"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID to scope EC2 permissions to"
  type        = string
  default     = "*"
}

