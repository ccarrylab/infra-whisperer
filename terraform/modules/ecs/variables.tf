variable "project_name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnets" {
  type = list(string)
}

variable "container_image" {
  type = string
}

variable "container_port" {
  type = number
}

variable "desired_count" {
  type = number
}

variable "alb_target_group_arn" {
  type = string
}

variable "alb_security_group_id" {
  type = string
}

variable "assign_public_ip" {
  description = "Set true when the subnets passed in are actually public (e.g. a default VPC with no private/NAT tier). ECS tasks need a public IP in that case to reach the internet for image pulls and outbound API calls."
  type        = bool
  default     = false
}
