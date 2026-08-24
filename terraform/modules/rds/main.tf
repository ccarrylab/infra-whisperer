resource "aws_db_subnet_group" "this" {
  name       = "${var.project}-db-subnets"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.project}-db-subnets"
  }
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.project}-pg16-params"
  family = "postgres16"

  # max_connections intentionally left at PostgreSQL default.
  # Default formula: LEAST({DBInstanceClassMemory/9531392}, 5000)
  # On db.t4g.micro (1 GB RAM) this yields ~107 connections — safe for production.
  # Do NOT re-add a hard-coded low value here for chaos testing without a follow-up revert PR.
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db-sg"
  description = "RDS SG - only the ECS service SG may connect. Chaos scenario connection_pool exhausts this."
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from ECS service only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.ecs_service_sg_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project}-db-sg"
  }
}

resource "aws_db_instance" "this" {
  identifier        = "${var.project}-db"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = var.db_instance_class
  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = replace(var.project, "-", "")
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.this.name
  parameter_group_name   = aws_db_parameter_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]

  publicly_accessible = false
  skip_final_snapshot = true
  deletion_protection = false

  tags = {
    Name = "${var.project}-db"
  }
}
