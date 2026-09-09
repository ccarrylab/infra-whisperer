# Remote state backend - commented out until the migration described in the
# README is actually performed. The safety module already provisions the S3
# bucket and DynamoDB lock table these values reference (see
# `terraform output tf_state_bucket` / `tf_lock_table`), but nothing has been
# migrated to use them yet - every apply so far has used local state.
#
# To migrate (do this carefully, ideally with a state backup first):
#   1. Uncomment the block below
#   2. Run: terraform init -migrate-state
#   3. Confirm when prompted to copy existing state into the new backend
#
# terraform {
#   backend "s3" {
#     bucket         = "infra-whisperer-terraform-state-prod-9d49e428"
#     key            = "infra-whisperer/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "infra-whisperer-terraform-locks-prod"
#     encrypt        = true
#   }
# }
