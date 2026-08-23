terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment and configure once you have an S3 bucket for state.
  # The agent's read_tf_state tool reads from this backend, so a
  # remote backend (not local state) is required for the demo to work
  # end-to-end.
  #
  # backend "s3" {
  #   bucket = "cohen-infra-whisperer-tfstate"
  #   key    = "infra-whisperer/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "infra-whisperer"
      ManagedBy = "terraform"
    }
  }
}
