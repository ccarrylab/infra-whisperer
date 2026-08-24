resource "aws_db_parameter_group" "this" {
  name   = "infra-whisperer-pg16-params"
  family = "postgres16"

  parameter {
    name         = "max_connections"
    value        = "100"
    apply_method = "pending-reboot"
  }

  tags = {
    ManagedBy = "terraform"
    Project   = "infra-whisperer"
  }
}

resource "aws_db_subnet_group" "this" {
  name       = "infra-whisperer-db-subnets"
  subnet_ids = var.subnet_ids

  tags = {
    ManagedBy = "terraform"
    Project   = "infra-whisperer"
  }
}

resource "aws_security_group" "db" {
  name        = "infra-whisperer-db-sg"
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
    Name      = "infra-whisperer-db-sg"
    ManagedBy = "terraform"
    Project   = "infra-whisperer"
  }
}

resource "aws_db_instance" "this" {
  identifier        = "infra-whisperer-db"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = "db.t4g.micro"
  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = "infrawhisperer"
  username = "iwadmin"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.this.name

  publicly_accessible = false
  skip_final_snapshot = true
  deletion_protection = false

  tags = {
    Name      = "infra-whisperer-db"
    ManagedBy = "terraform"
    Project   = "infra-whisperer"
  }
}
