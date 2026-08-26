resource "aws_security_group" "service" {
  name        = "infra-whisperer-svc-sg"
  description = "ECS service SG - allows inbound HTTP from ALB only"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "infra-whisperer-svc-sg"
  }
}

# Explicitly declared as a standalone rule to ensure Terraform detects and
# corrects any out-of-band drift on this critical health-check path.
resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = var.alb_security_group_id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
  description                  = "From ALB only"

  depends_on = [
    aws_security_group.service,
  ]
}
