resource "aws_security_group" "service" {
  description = "ECS service SG - ALB-to-container traffic on port 80 only"
  name        = "${var.project}-svc-sg"
  vpc_id      = var.vpc_id

  # Inline ingress/egress blocks removed in favour of explicit standalone
  # rule resources below. This ensures Terraform will detect and correct
  # any out-of-band mutation (e.g. a chaos scenario that deletes the rule).
  lifecycle {
    ignore_changes = []   # no exemptions — we want full drift detection
  }

  tags = {
    Name = "${var.project}-svc-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "Allow inbound HTTP from ALB only"
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
  referenced_security_group_id = var.alb_security_group_id
}

resource "aws_vpc_security_group_egress_rule" "service_egress_all" {
  security_group_id = aws_security_group.service.id
  description       = "Allow all outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
