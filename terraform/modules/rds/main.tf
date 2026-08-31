resource "aws_db_parameter_group" "this" {
  name   = "infra-whisperer-pg16-params"
  family = "postgres16"

  description = "Managed by Terraform"

  parameter {
    name         = "max_connections"
    value        = "100"
    apply_method = "pending-reboot"
  }

  tags = {}
}
