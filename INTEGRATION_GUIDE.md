# Production Safety & Chaos Improvements — Integration Guide

This guide explains how to integrate the four new modules into your existing `infra-whisperer` repo.

## Files Overview

| File | Purpose | Drop-in Location |
|------|---------|------------------|
| `confidence_validator.py` | Evidence-based, tunable confidence rubric | `agent/confidence_validator.py` |
| `tf_state_parser.py` | Semantic Terraform state parser (fixes SG rule blind spot) | `agent/tf_state_parser.py` |
| `safety.tf` + `safety_variables.tf` | Production safety infrastructure | `terraform/modules/safety/` |
| `terraform-apply.yml` | Multi-step approval GitHub Actions workflow | `.github/workflows/terraform-apply.yml` |
| `chaos_inject_tf.py` | Chaos injection with Terraform state consistency | `chaos/chaos_inject_tf.py` |

---

## 1. Confidence Validator Integration

### What it replaces
Your current `score_diagnosis_confidence` tool (or the free-form confidence prompting).

### How to integrate

1. Copy `confidence_validator.py` into `agent/`
2. In `agent/tools.py`, replace the confidence tool definition:

```python
# OLD: Free-form confidence
# "score_diagnosis_confidence": "Return a confidence percentage..."

# NEW: Deterministic rubric
from confidence_validator import ConfidenceRubric, EvidenceFlag

rubric = ConfidenceRubric()

def score_diagnosis_confidence(evidence_flags: list[str]) -> dict:
    """
    Score diagnosis confidence based on evidence.

    Args:
        evidence_flags: List of evidence types present. Must be honest.
            Options: cloudwatch_alarm_correlates, terraform_state_confirms,
                     logs_confirm, ecs_events_correlate, independent_second_signal,
                     temporal_coincidence_only, contradicting_evidence

    Returns:
        Dict with score, verdict, and whether human verification is required.
    """
    flags = [EvidenceFlag(f) for f in evidence_flags]
    result = rubric.score(flags)

    return {
        "score": result.score,
        "verdict": result.verdict,
        "requires_human_verification": result.requires_human_verification,
        "reasoning": result.reasoning,
        "evidence_breakdown": result.evidence_breakdown,
    }
```

3. **Critical**: Update the agent's system prompt to require calling this tool before stating any confidence level. The agent must pass honest evidence flags — false flags are an integrity issue.

4. Run validation against your history:
```bash
cd agent && python confidence_validator.py
```

This prints calibration metrics. If you add new incidents, add them to `HISTORICAL_INCIDENTS` and re-run.

---

## 2. Terraform State Parser Integration

### What it fixes
The blind spot where a security group rule was refactored from inline to a separate `aws_security_group_rule` resource, and the agent thought it was missing.

### How to integrate

1. Copy `tf_state_parser.py` into `agent/`
2. In `agent/tools.py`, enhance the `read_terraform_state` tool:

```python
from tf_state_parser import TerraformStateParser, tool_read_terraform_state_enhanced

# Replace or wrap your existing read_terraform_state tool
def read_terraform_state(query_type: str = "security_group", target: str = "") -> str:
    """
    Read and analyze Terraform state with semantic understanding.

    query_type options:
        - "security_group": Analyze a security group and all its rules (inline + external)
        - "drift_check": Compare expected rules against actual state
        - "resource_graph": Show what references what

    target: Resource address (e.g., "aws_security_group.ecs_service")
    """
    state_path = "terraform/terraform.tfstate"  # or your state file path
    return tool_read_terraform_state_enhanced(state_path, query_type, target)
```

3. Update the agent's reasoning prompt to ask:
   - "Does the security group have the expected capability, regardless of how it's defined?"
   - "Where is the rule actually defined — inline or external?"
   - "Is this genuine drift or just a refactored resource?"

4. Test against your known blind spot:
```bash
cd agent && python tf_state_parser.py
```

---

## 3. Production Safety Infrastructure

### What it adds
- S3 + DynamoDB remote state backend with locking
- Three scoped IAM roles (diagnosis, plan, apply)
- GitHub OIDC for credential-less Actions authentication
- Multi-step approval workflow (CODEOWNERS → plan → environment approval → apply)

### How to integrate

1. Create `terraform/modules/safety/` and copy `safety.tf` and `safety_variables.tf`
2. Wire it into your root module:

```hcl
# terraform/main.tf
module "safety" {
  source = "./modules/safety"

  project_name    = var.project_name
  environment     = var.environment

  # The principal that runs the agent (EC2 instance role, ECS task role, etc.)
  trusted_principal_arn = var.agent_execution_role_arn

  github_org  = "ccarrylab"
  github_repo = "infra-whisperer"

  # Scope permissions to your actual resources
  ecs_cluster_arn        = module.ecs.cluster_arn
  rds_instance_arn       = module.rds.instance_arn
  ecs_execution_role_arn = module.ecs.execution_role_arn
}
```

3. Add outputs to your root module:

```hcl
# terraform/outputs.tf
output "agent_diagnosis_role_arn" {
  value = module.safety.agent_diagnosis_role_arn
}

output "agent_plan_role_arn" {
  value = module.safety.agent_plan_role_arn
}

output "agent_apply_role_arn" {
  value = module.safety.agent_apply_role_arn
  sensitive = true  # Don't log this in CI
}

output "terraform_state_bucket" {
  value = module.safety.s3_bucket_name
}

output "terraform_lock_table" {
  value = module.safety.dynamodb_table_name
}
```

4. Update agent environment variables:
```bash
# Instead of broad AWS credentials:
# export AWS_ACCESS_KEY_ID=...
# export AWS_SECRET_ACCESS_KEY=...

# Use role assumption:
export AGENT_DIAGNOSIS_ROLE_ARN=arn:aws:iam::ACCOUNT:role/infra-whisperer-agent-diagnosis-prod
export AGENT_PLAN_ROLE_ARN=arn:aws:iam::ACCOUNT:role/infra-whisperer-agent-plan-prod
# AGENT_APPLY_ROLE_ARN is NEVER set in the agent environment — only GitHub Actions uses it
```

5. Update the agent to assume roles:

```python
# agent/aws_auth.py
import boto3

def assume_role(role_arn: str, session_name: str = "infra-whisperer-agent") -> boto3.Session:
    sts = boto3.client("sts")
    assumed = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=3600,
    )
    creds = assumed["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )

# In agent.py:
diagnosis_session = assume_role(os.environ["AGENT_DIAGNOSIS_ROLE_ARN"])
plan_session = assume_role(os.environ["AGENT_PLAN_ROLE_ARN"])
# No apply_session — the agent never applies changes
```

6. Configure GitHub repository:
   - Go to Settings → Environments → Create `production`
   - Add protection rule: Required reviewers = 1 (or 2)
   - Add protection rule: Deployment branches = `main`
   - Go to Settings → Secrets and variables → Actions → Variables
   - Add: `AGENT_PLAN_ROLE_ARN`, `AGENT_APPLY_ROLE_ARN`, `TF_STATE_BUCKET`, `TF_LOCK_TABLE`, `AWS_REGION`

7. Copy `.github/workflows/terraform-apply.yml`

---

## 4. Chaos Script Improvements

### What it fixes
Original chaos scripts mutate live AWS resources via boto3, causing Terraform state drift. This causes:
- Agent's `terraform plan` check to show unexpected drift
- Repeated demo runs to accumulate confusing state
- Branch-name collision bugs (same failure pattern, different runs)

### How to integrate

**Option A: Terraform-based chaos (recommended for demos)**

```bash
# Inject failure
python chaos/chaos_inject_tf.py --approach terraform --scenario security_group

# Run agent diagnosis
python agent/agent.py --diagnose-now

# Clean up (removes override, restores state)
python chaos/chaos_inject_tf.py --approach terraform --cleanup
```

**Option B: Boto3 with auto-refresh (for existing scripts)**

```bash
# Wraps your existing inject.py with automatic terraform refresh
python chaos/chaos_inject_tf.py --approach boto3 --scenario security_group

# State is automatically refreshed and diff is shown
# Clean up with terraform apply
python chaos/chaos_inject_tf.py --approach boto3 --cleanup
```

### Integration with existing demo.sh

Update your `demo.sh`:

```bash
#!/bin/bash
set -e

echo "=== Infra-Whisperer Live Demo ==="

# 1. Ensure clean state
echo "[1/5] Ensuring clean Terraform state..."
cd terraform
terraform init
terraform apply -auto-approve  # Ensure no leftover chaos
cd ..

# 2. Inject chaos via Terraform (state-consistent)
echo "[2/5] Injecting chaos scenario..."
python chaos/chaos_inject_tf.py --approach terraform --scenario security_group

# 3. Run agent diagnosis
echo "[3/5] Running agent diagnosis..."
cd agent
python agent.py --diagnose-now
cd ..

# 4. Verify PR was opened
echo "[4/5] Checking for open PR..."
gh pr list --state open

# 5. Cleanup
echo "[5/5] Cleaning up chaos..."
python chaos/chaos_inject_tf.py --approach terraform --cleanup

echo "=== Demo complete ==="
```

---

## 5. Updated Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           HUMAN TEAM                                        │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                    │
│  │  Incident   │───→│ CODEOWNERS  │───→│  Production │                    │
│  │  detected   │    │   approves  │    │  env approves│                   │
│  └─────────────┘    └─────────────┘    └─────────────┘                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AGENT (Read-Only)                                   │
│  ┌─────────────┐    ┌─────────────────────┐    ┌─────────────────────┐     │
│  │ CloudWatch  │───→│  Confidence Rubric  │───→│   Diagnosis Report  │     │
│  │   Alarms    │    │  (deterministic,    │    │  (VP-readable +     │     │
│  └─────────────┘    │   evidence-based)   │    │   PR with diff)     │     │
│  ┌─────────────┐    └─────────────────────┘    └─────────────────────┘     │
│  │  ECS Events │                                                            │
│  └─────────────┘                                                            │
│  ┌─────────────┐    ┌─────────────────────┐                                │
│  │   TF State  │───→│  Semantic Parser    │                                │
│  │  (enhanced) │    │  (effective rules,   │                                │
│  └─────────────┘    │   not just inline)   │                                │
│                     └─────────────────────┘                                │
│  Role: infra-whisperer-agent-diagnosis-prod                                │
│  (CloudWatch read, ECS read, IAM read, EC2 read, RDS read)                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      GITHUB ACTIONS (Plan-Only)                             │
│  ┌─────────────┐    ┌─────────────────────┐    ┌─────────────────────┐     │
│  │  PR opened  │───→│  terraform plan     │───→│  Plan comment on    │     │
│  │  by agent   │    │  (read-only validate)│   │  PR                 │     │
│  └─────────────┘    └─────────────────────┘    └─────────────────────┘     │
│  Role: infra-whisperer-agent-plan-prod                                     │
│  (State read/write for plan, DynamoDB lock, no apply)                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼ (after 2nd human approval)
┌─────────────────────────────────────────────────────────────────────────────┐
│                      GITHUB ACTIONS (Apply)                                 │
│  ┌─────────────┐    ┌─────────────────────┐    ┌─────────────────────┐     │
│  │  Environment│───→│  terraform apply    │───→│  Post-apply health  │     │
│  │  approved   │    │  (with state lock)  │    │  checks             │     │
│  └─────────────┘    └─────────────────────┘    └─────────────────────┘     │
│  Role: infra-whisperer-agent-apply-prod                                    │
│  (OIDC from GitHub only, time-bounded, scoped to specific resources)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Testing Checklist

Before claiming this is production-ready:

- [ ] Run `python confidence_validator.py` — verify all 4 historical incidents classify correctly
- [ ] Run `python tf_state_parser.py` — verify the SG blind spot is resolved
- [ ] Deploy `terraform/modules/safety` to a test AWS account
- [ ] Verify agent can assume diagnosis role and read CloudWatch/ECS
- [ ] Verify agent can assume plan role and run `terraform plan` against remote backend
- [ ] Verify GitHub Actions can assume apply role via OIDC
- [ ] Test the full pipeline: chaos injection → agent diagnosis → PR → plan → approval → apply
- [ ] Verify state locking works (run two plans simultaneously, one should wait)
- [ ] Verify the agent CANNOT apply changes even if compromised (no apply role credentials)
- [ ] Test chaos cleanup: run injection → diagnosis → cleanup → verify state is clean
- [ ] Add a 5th incident to HISTORICAL_INCIDENTS and re-validate rubric calibration

---

## 7. What This Doesn't Fix (Yet)

- **The agent's causal reasoning**: It can still weave real events into wrong stories. The rubric catches this by scoring low on `temporal_coincidence_only`, but the underlying LLM tendency remains.
- **Cost controls**: The budget alarm is still a good idea, but production needs more granular cost alerts per service.
- **Multi-region**: Everything here assumes a single region. Multi-region incidents need a different architecture.
- **Rollback**: If apply fails post-approval, there's no automatic rollback yet. The workflow notifies on failure but doesn't self-heal.

---

## 8. Next Steps

1. Integrate these files into your repo
2. Run the validation scripts
3. Test against your live stack (with the $0 cost discipline)
4. Add the 5th incident to the rubric when you find it
5. Update the README with the new architecture diagram

The goal isn't to make this "production-grade" overnight — it's to make the *gap* between demo and production explicit and measurable. These modules do that.
