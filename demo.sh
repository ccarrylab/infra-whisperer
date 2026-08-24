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

set -euo pipefail

CLUSTER="infra-whisperer-cluster"
SERVICE="infra-whisperer-service"
SG_ID="sg-0071b653c8bc174c9"

REQUIRED_VARS=(ANTHROPIC_API_KEY GITHUB_TOKEN GITHUB_REPO)
for v in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!v:-}" ]; then
    echo "Missing required env var: $v"
    echo "Export it before running this script."
    exit 1
  fi
done

echo "=== Infra Whisperer Live Demo ==="
echo ""
echo "Step 1/4: Confirming baseline healthy state..."
BASELINE=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query "services[0].[desiredCount,runningCount,pendingCount]" --output text)
echo "  desired/running/pending: $BASELINE"
echo ""

echo "Step 2/4: Injecting real failure (revoking ALB->ECS security group ingress rule)..."
(cd chaos && python3 inject.py --scenario security_group --sg-id "$SG_ID")
echo ""

echo "Step 3/4: Waiting for CloudWatch to detect the incident (needs ~2 evaluation periods)..."
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

echo "Step 4/4: Running the agent to diagnose..."
(cd agent && python3 agent.py --diagnose-now)
echo ""

echo "=== Demo complete ==="
echo ""
echo "To fix the real incident and restore service:"
echo "  cd terraform && terraform plan -out=tfplan && terraform apply tfplan"
echo ""
echo "To tear down entirely and stop all AWS charges:"
echo "  cd terraform && terraform destroy"
