#!/usr/bin/env bash
# Infra Whisperer — scripted end-to-end demo.
#
# Runs the full loop against REAL AWS infrastructure: injects a real failure,
# waits for CloudWatch to detect it, then runs the agent to diagnose and
# open a PR.
#
# Usage:
#   ./demo.sh
#
# Requires (export before running):
#   ANTHROPIC_API_KEY, GITHUB_TOKEN, GITHUB_REPO
#   AGENT_DIAGNOSIS_ROLE_ARN, AGENT_PLAN_ROLE_ARN (from safety module deploy)

set -euo pipefail

CLUSTER="infra-whisperer-cluster"
SERVICE="infra-whisperer-service"

REQUIRED_VARS=(ANTHROPIC_API_KEY GITHUB_TOKEN GITHUB_REPO AGENT_DIAGNOSIS_ROLE_ARN AGENT_PLAN_ROLE_ARN)
for v in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!v:-}" ]; then
    echo "Missing required env var: $v"
    echo "Export it before running this script."
    echo ""
    echo "For AGENT_*_ROLE_ARN, get them from:"
    echo "  cd terraform && terraform output agent_diagnosis_role_arn"
    echo "  cd terraform && terraform output agent_plan_role_arn"
    exit 1
  fi
done

echo "=== Infra Whisperer Live Demo ==="
echo ""

echo "Step 1/5: Ensuring clean Terraform state..."
cd terraform
terraform init > /dev/null 2>&1 || true
terraform apply -auto-approve > /dev/null 2>&1 || true
cd ..
echo "  Baseline restored."
echo ""

echo "Step 2/5: Injecting real failure (revoking ALB->ECS security group ingress rule)..."
python chaos/chaos_inject_tf.py --approach terraform --scenario security_group
echo "  Chaos injected via Terraform (state-consistent)."
echo ""

echo "Step 3/5: Waiting for CloudWatch to detect the incident (needs ~2 evaluation periods)..."
for i in $(seq 1 30); do
  sleep 10
  STATE=$(aws cloudwatch describe-alarms --alarm-names infra-whisperer-unhealthy-targets \
    --query "MetricAlarms[0].StateValue" --output text)
  echo "  [$((i*10))s] unhealthy_targets alarm: $STATE"
  if [ "$STATE" == "ALARM" ]; then
    echo "  Incident confirmed."
    break
  fi
done
echo ""

echo "Step 4/5: Running the agent to diagnose..."
(cd agent && python3 agent.py --diagnose-now)
echo ""

echo "Step 5/5: Checking for open PR..."
gh pr list --state open
echo ""

echo "=== Demo complete ==="
echo ""
echo "To restore service and clean up chaos:"
echo "  python chaos/chaos_inject_tf.py --approach terraform --cleanup"
echo "  cd terraform && terraform apply -auto-approve"
echo ""
echo "To tear down entirely and stop all AWS charges:"
echo "  cd terraform && terraform destroy"