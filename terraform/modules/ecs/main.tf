resource "aws_security_group" "service" {
  name        = "infra-whisperer-svc-sg"
  description = "ECS service SG - the chaos scenario security_group mutates this rule"
  vpc_id      = var.vpc_id

  ingress {
    description     = "From ALB only"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "infra-whisperer-svc-sg"
  }

  tags_all = {
    ManagedBy = "terraform"
    Name      = "infra-whisperer-svc-sg"
    Project   = "infra-whisperer"
  }
}

# NOTE: The standalone aws_vpc_security_group_ingress_rule.service_from_alb
# resource should be REMOVED alongside this change to avoid a duplicate rule.
# The ingress is now managed inline above, which makes it self-healing.
