resource "aws_db_subnet_group" "this" {
  name       = "${var.project_name}-db-subnets"
  subnet_ids = var.private_subnets
}

resource "aws_security_group" "db" {
  name        = "${var.project_name}-db-sg"
  description = "RDS SG - only the ECS service SG may connect. Chaos scenario connection_pool exhausts this."
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from ECS service only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.allowed_sg_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-db-sg" }
}

resource "aws_db_instance" "this" {
  identifier             = "${var.project_name}-db"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.db_instance_class
  allocated_storage      = 20
  db_name                = var.db_name
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]

  # Demo-project settings - keep cost near zero and allow easy teardown.
  skip_final_snapshot     = true
  deletion_protection     = false
  backup_retention_period = 0
  multi_az                = false
  publicly_accessible     = false

  # Deliberately low, realistic max_connections stress point for the
  # connection_pool chaos scenario.
  parameter_group_name = aws_db_parameter_group.this.name

  tags = { Name = "${var.project_name}-db" }
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.project_name}-pg16-params"
  family = "postgres16"

  parameter {
    name         = "max_connections"
    value        = "20"
    apply_method = "pending-reboot"
  }
}
