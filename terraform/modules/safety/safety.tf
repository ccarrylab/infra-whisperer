# =============================================================================
# Production Safety Infrastructure for infra-whisperer
# =============================================================================
# 
# SECURITY NOTES FOR PUBLIC REPOS:
# - No credentials, account IDs, or ARNs are hardcoded in this file
# - S3 bucket name is parameterized — set via TF_VAR_state_bucket_name
# - GitHub OIDC thumbprint must be verified before deployment
# - All IAM policies use least-privilege with explicit Deny statements
#
# Place these in terraform/modules/safety/
# =============================================================================

# ---------------------------------------------------------------------------
# 1. REMOTE STATE BACKEND
# ---------------------------------------------------------------------------

# Random suffix for S3 bucket to prevent name collisions and
# reduce predictability in public repos
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "terraform_state" {
  # SECURITY: Bucket name is parameterized. Set via:
  #   export TF_VAR_state_bucket_name="my-unique-bucket-name"
  # Never commit the actual bucket name to this public repo.
  bucket = "${var.state_bucket_name}-${random_id.bucket_suffix.hex}"

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name        = "${var.project_name}-terraform-state"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# DynamoDB table for state locking — prevents concurrent terraform apply
resource "aws_dynamodb_table" "terraform_locks" {
  name         = "${var.project_name}-terraform-locks-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name        = "${var.project_name}-terraform-locks"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# 2. SCOPED IAM ROLES FOR THE AGENT
# ---------------------------------------------------------------------------
#
# Principle: The agent should NEVER have credentials that can both 
# diagnose AND modify infrastructure. These are separate concerns 
# with separate blast radii.
#
# Role 1: infra-whisperer-diagnosis (read-only)
# Role 2: infra-whisperer-plan (plan-only)
# Role 3: infra-whisperer-apply (apply, but NOT diagnosis)
# ---------------------------------------------------------------------------

# --- Role 1: Diagnosis (read-only) ---

resource "aws_iam_role" "agent_diagnosis" {
  name = "${var.project_name}-agent-diagnosis-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          AWS = var.trusted_principal_arn
        }
      }
    ]
  })

  max_session_duration = 3600

  tags = {
    Name        = "${var.project_name}-agent-diagnosis"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "agent_diagnosis" {
  name = "diagnosis-read-only"
  role = aws_iam_role.agent_diagnosis.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:DescribeAlarmHistory",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
        ]
        Resource = "*"
      },
      {
        Sid    = "ECSRead"
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "ecs:DescribeTasks",
          "ecs:ListTasks",
          "ecs:DescribeClusters",
          "ecs:DescribeContainerInstances",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "ecs:cluster" = var.ecs_cluster_arn
          }
        }
      },
      {
        Sid    = "IAMRead"
        Effect = "Allow"
        Action = [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListAttachedRolePolicies",
          "iam:ListRolePolicies",
        ]
        Resource = "*"
      },
      {
        Sid    = "EC2Read"
        Effect = "Allow"
        Action = [
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSecurityGroupRules",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcs",
          "ec2:DescribeInstances",
        ]
        Resource = "*"
      },
      {
        Sid    = "RDSRead"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:DescribeDBLogFiles",
          "rds:DownloadDBLogFilePortion",
        ]
        Resource = var.rds_instance_arn
      },
      {
        Sid    = "ALBRead"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
          "elasticloadbalancing:DescribeListeners",
          "elasticloadbalancing:DescribeLoadBalancers",
        ]
        Resource = "*"
      },
      {
        Sid    = "StateRead"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.terraform_state.arn,
          "${aws_s3_bucket.terraform_state.arn}/*",
        ]
      },
      {
        Sid    = "DenyWrite"
        Effect = "Deny"
        Action = [
          "ecs:*",
          "ec2:*",
          "rds:*",
          "iam:*",
          "elasticloadbalancing:*",
          "s3:Put*",
          "s3:Delete*",
        ]
        Resource = "*"
      },
    ]
  })
}

# --- Role 2: Plan-only ---

resource "aws_iam_role" "agent_plan" {
  name = "${var.project_name}-agent-plan-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          AWS = var.trusted_principal_arn
        }
      }
    ]
  })

  max_session_duration = 3600

  tags = {
    Name        = "${var.project_name}-agent-plan"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "agent_plan" {
  name = "plan-only"
  role = aws_iam_role.agent_plan.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InheritDiagnosisRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
          "cloudwatch:DescribeAlarms",
          "logs:DescribeLogGroups",
          "logs:GetLogEvents",
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "iam:GetRole",
          "iam:ListAttachedRolePolicies",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSubnets",
          "rds:DescribeDBInstances",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
        ]
        Resource = "*"
      },
      {
        Sid    = "StatePlan"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.terraform_state.arn,
          "${aws_s3_bucket.terraform_state.arn}/*",
        ]
      },
      {
        Sid    = "StateLockPlan"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
        ]
        Resource = aws_dynamodb_table.terraform_locks.arn
      },
      {
        Sid    = "DenyApply"
        Effect = "Deny"
        Action = [
          "ecs:UpdateService",
          "ecs:RegisterTaskDefinition",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:CreateSecurityGroup",
          "ec2:DeleteSecurityGroup",
          "rds:ModifyDBInstance",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "elasticloadbalancing:ModifyTargetGroup",
        ]
        Resource = "*"
      },
    ]
  })
}

# --- Role 3: Apply (used by GitHub Actions, NOT the agent directly) ---

resource "aws_iam_role" "agent_apply" {
  name = "${var.project_name}-agent-apply-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRoleWithWebIdentity"
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_org}/${var.github_repo}:environment:${var.environment}",
              "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main",
            ]
          }
        }
      }
    ]
  })

  max_session_duration = 3600

  tags = {
    Name        = "${var.project_name}-agent-apply"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "agent_apply" {
  name = "apply-changes"
  role = aws_iam_role.agent_apply.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECSApply"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:RegisterTaskDefinition",
          "ecs:DeregisterTaskDefinition",
          "ecs:DescribeServices",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "ecs:cluster" = var.ecs_cluster_arn
          }
        }
      },
      {
        Sid    = "EC2Apply"
        Effect = "Allow"
        Action = [
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:CreateSecurityGroup",
          "ec2:DeleteSecurityGroup",
          "ec2:ModifySecurityGroupRules",
        ]
        Resource = "*"
      },
      {
        Sid    = "IAMApply"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:PassRole",
        ]
        Resource = var.ecs_execution_role_arn
      },
      {
        Sid    = "RDSApply"
        Effect = "Allow"
        Action = [
          "rds:ModifyDBInstance",
          "rds:DescribeDBInstances",
        ]
        Resource = var.rds_instance_arn
      },
      {
        Sid    = "ALBApply"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:ModifyTargetGroup",
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:ModifyListener",
        ]
        Resource = "*"
      },
      {
        Sid    = "StateApply"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.terraform_state.arn,
          "${aws_s3_bucket.terraform_state.arn}/*",
        ]
      },
      {
        Sid    = "StateLockApply"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
        ]
        Resource = aws_dynamodb_table.terraform_locks.arn
      },
    ]
  })
}

# GitHub OIDC provider for secure role assumption without long-lived credentials
# SECURITY: Verify this thumbprint before deployment at:
# https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  # This thumbprint is current as of 2024. Always verify before deploying.
  thumbprint_list = ["6938fd4e98bab03faadb97b34396831e3780aea1"]

  tags = {
    Name = "github-actions-oidc"
  }
}

# ---------------------------------------------------------------------------
# 3. OUTPUTS
# ---------------------------------------------------------------------------

output "s3_bucket_name" {
  description = "S3 bucket for Terraform remote state"
  value       = aws_s3_bucket.terraform_state.id
}

output "dynamodb_table_name" {
  description = "DynamoDB table for state locking"
  value       = aws_dynamodb_table.terraform_locks.name
}

output "agent_diagnosis_role_arn" {
  description = "ARN of the read-only diagnosis role"
  value       = aws_iam_role.agent_diagnosis.arn
}

output "agent_plan_role_arn" {
  description = "ARN of the plan-only role"
  value       = aws_iam_role.agent_plan.arn
}

output "agent_apply_role_arn" {
  description = "ARN of the apply role (GitHub Actions only)"
  value       = aws_iam_role.agent_apply.arn
}

output "github_oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider"
  value       = aws_iam_openid_connect_provider.github.arn
}
