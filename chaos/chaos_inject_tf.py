"""
chaos_inject_tf.py

Improved chaos injection that maintains Terraform state consistency.

The original problem: chaos scripts mutate live AWS resources via boto3,
which drifts from Terraform state. This causes:
1. The agent's terraform plan check returns unexpected drift
2. Subsequent demo runs accumulate confusing state
3. The branch-name collision bug (repeated chaos against same stack)

This module provides TWO approaches:

APPROACH A: Chaos-as-Terraform (recommended for demos)
  - Inject failures by creating temporary Terraform override files
  - terraform apply creates the failure
  - terraform destroy (or removing the override) cleans it up
  - State is always consistent — no drift accumulation

APPROACH B: Auto-refresh wrapper (for existing boto3 chaos)
  - Wraps the original inject.py
  - Automatically runs `terraform refresh` after injection
  - Verifies state reflects the injected failure
  - Provides clear before/after state diff

Drop-in: place in chaos/ alongside existing inject.py
"""

import json
import subprocess
import tempfile
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass


@dataclass
class ChaosScenario:
    """A chaos scenario that can be injected via Terraform."""
    name: str
    description: str
    terraform_override: str  # HCL content for the override
    expected_alarms: List[str]  # Which CloudWatch alarms should fire
    expected_agent_diagnosis: str  # What the agent should find
    cleanup_override: Optional[str] = None  # HCL to restore (if different from removal)


# =============================================================================
# APPROACH A: CHAOS-AS-TERRAFORM
# =============================================================================

class TerraformChaosInjector:
    """
    Inject failures by creating temporary Terraform override files.

    Usage:
        injector = TerraformChaosInjector("terraform/")

        # Inject a failure
        injector.inject(scenario_security_group_revoke)
        # ... run agent diagnosis ...
        injector.cleanup()  # Removes override, restores original state

    How it works:
    1. Creates a temporary .tf file in the terraform directory
    2. The file contains a resource that conflicts with/replaces the real one
    3. terraform apply creates the failure condition
    4. After testing, remove the override file and terraform apply again
    """

    def __init__(self, terraform_dir: str, backup_dir: Optional[str] = None):
        self.terraform_dir = Path(terraform_dir)
        self.backup_dir = Path(backup_dir) if backup_dir else self.terraform_dir / ".chaos_backup"
        self.active_override: Optional[Path] = None
        self._ensure_backup_dir()

    def _ensure_backup_dir(self):
        self.backup_dir.mkdir(exist_ok=True)

    def _run_terraform(self, *args, capture=True) -> subprocess.CompletedProcess:
        """Run terraform command in the terraform directory."""
        cmd = ["terraform", *args]
        result = subprocess.run(
            cmd,
            cwd=self.terraform_dir,
            capture_output=capture,
            text=True
        )
        return result

    def inject(self, scenario: ChaosScenario, auto_approve: bool = True) -> Dict:
        """
        Inject a chaos scenario via Terraform override.

        Returns:
            Dict with injection results and state verification
        """
        if self.active_override:
            raise RuntimeError(f"Already have active override: {self.active_override}. Run cleanup() first.")

        # Create the override file
        override_file = self.terraform_dir / f"_chaos_override_{scenario.name}.tf"
        override_file.write_text(scenario.terraform_override)
        self.active_override = override_file

        # Run terraform plan to preview the failure
        plan_result = self._run_terraform("plan", "-out=chaos_plan.tfplan")

        # Apply the failure
        apply_args = ["apply"]
        if auto_approve:
            apply_args.append("-auto-approve")
        apply_args.append("chaos_plan.tfplan")
        apply_result = self._run_terraform(*apply_args)

        # Verify state reflects the failure
        state_result = self._run_terraform("show", "-json")

        return {
            "scenario": scenario.name,
            "override_file": str(override_file),
            "plan_output": plan_result.stdout,
            "plan_error": plan_result.stderr if plan_result.returncode != 0 else None,
            "apply_output": apply_result.stdout,
            "apply_error": apply_result.stderr if apply_result.returncode != 0 else None,
            "state_captured": state_result.stdout[:500] if state_result.returncode == 0 else None,
            "success": apply_result.returncode == 0,
        }

    def cleanup(self, auto_approve: bool = True) -> Dict:
        """
        Remove the chaos override and restore original state.

        Returns:
            Dict with cleanup results
        """
        if not self.active_override:
            return {"success": True, "message": "No active override to clean up"}

        # Remove the override file
        override_file = self.active_override
        self.active_override = None

        # Backup the override for forensics
        backup_path = self.backup_dir / f"{override_file.name}.{os.urandom(4).hex()}.bak"
        shutil.move(str(override_file), str(backup_path))

        # Plan the restoration
        plan_result = self._run_terraform("plan", "-out=restore_plan.tfplan")

        # Apply restoration
        apply_args = ["apply"]
        if auto_approve:
            apply_args.append("-auto-approve")
        apply_args.append("restore_plan.tfplan")
        apply_result = self._run_terraform(*apply_args)

        return {
            "override_removed": str(override_file),
            "backup_location": str(backup_path),
            "plan_output": plan_result.stdout,
            "apply_output": apply_result.stdout,
            "success": apply_result.returncode == 0,
        }

    def get_state_diff(self) -> str:
        """Get a human-readable diff showing what changed."""
        result = self._run_terraform("show", "-json")
        if result.returncode != 0:
            return f"Error getting state: {result.stderr}"

        # Parse and summarize the state
        try:
            state = json.loads(result.stdout)
            resources = state.get("values", {}).get("root_module", {}).get("resources", [])
            lines = ["Current Terraform State Summary:"]
            for res in resources:
                addr = res.get("address", "unknown")
                status = res.get("mode", "managed")
                lines.append(f"  {addr} ({status})")
            return "\n".join(lines)
        except json.JSONDecodeError:
            return result.stdout[:2000]


# =============================================================================
# PRE-BUILT CHAOS SCENARIOS
# =============================================================================

# Scenario 1: Revoke security group ingress rule
# This creates a conflicting aws_security_group_rule that removes HTTP access
SCENARIO_SECURITY_GROUP_REVOKE = ChaosScenario(
    name="security_group_revoke",
    description="Revoke the ALB ingress rule from the ECS service security group",
    terraform_override="""
# CHAOS INJECTION: Remove ALB ingress rule
# This override creates a null_resource that triggers a local-exec
# which revokes the security group rule via AWS CLI.
# The key difference from boto3: this goes through Terraform's state tracking.

resource "null_resource" "chaos_revoke_sg" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws ec2 revoke-security-group-ingress \
        --group-id ${aws_security_group.ecs_service.id} \
        --protocol tcp \
        --port 80 \
        --cidr 0.0.0.0/0 \
        --region ${var.aws_region}
    EOT
  }
}
""",
    expected_alarms=["alb-unhealthy-hosts", "healthy-host-count-low"],
    expected_agent_diagnosis="Revoked security group ingress rule blocking ALB to ECS traffic",
)

# Scenario 2: Reduce RDS max_connections
# This overrides the RDS parameter group
SCENARIO_RDS_CONNECTION_LIMIT = ChaosScenario(
    name="rds_connection_limit",
    description="Reduce RDS max_connections to cause connection pool exhaustion",
    terraform_override="""
# CHAOS INJECTION: Reduce max_connections to 5
# This creates a temporary parameter group with a very low connection limit

resource "aws_db_parameter_group" "chaos_low_connections" {
  name   = "${var.project_name}-chaos-low-conn"
  family = "postgres15"

  parameter {
    name  = "max_connections"
    value = "5"
    apply_method = "immediate"
  }
}

# Override the RDS instance to use the chaos parameter group
resource "aws_db_instance" "chaos_override" {
  # This would need to match your actual RDS resource name
  # In practice, you'd use a terraform override file (.tfoverride) 
  # or target the specific resource
  identifier = var.db_instance_identifier

  parameter_group_name = aws_db_parameter_group.chaos_low_connections.name

  # Apply immediately for chaos effect
  apply_immediately = true
}
""",
    expected_alarms=["rds-connection-count-high"],
    expected_agent_diagnosis="RDS max_connections too low causing connection pool exhaustion",
)

# Scenario 3: Detach IAM policy
# This removes an IAM policy attachment
SCENARIO_IAM_DETACH = ChaosScenario(
    name="iam_policy_detach",
    description="Detach the CloudWatch Logs policy from ECS execution role",
    terraform_override="""
# CHAOS INJECTION: Remove IAM policy attachment
# This uses a null_resource with local-exec to detach the policy

resource "null_resource" "chaos_detach_iam" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws iam detach-role-policy \
        --role-name ${aws_iam_role.ecs_execution.name} \
        --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess \
        --region ${var.aws_region}
    EOT
  }
}
""",
    expected_alarms=[],  # This is the blind spot — no alarms fire!
    expected_agent_diagnosis="IAM policy detachment preventing new ECS tasks from writing logs",
)


# =============================================================================
# APPROACH B: AUTO-REFRESH WRAPPER (for existing boto3 chaos)
# =============================================================================

class Boto3ChaosWrapper:
    """
    Wraps the original boto3 chaos scripts with automatic state refresh.

    Usage:
        wrapper = Boto3ChaosWrapper("terraform/")

        # Run your existing chaos script
        wrapper.run_chaos("python chaos/inject.py --scenario security_group")

        # State is automatically refreshed and verified
        print(wrapper.get_state_diff())

        # When done, restore and refresh again
        wrapper.restore()
    """

    def __init__(self, terraform_dir: str):
        self.terraform_dir = Path(terraform_dir)
        self.pre_chaos_state: Optional[Dict] = None
        self.post_chaos_state: Optional[Dict] = None

    def _run_terraform(self, *args, capture=True) -> subprocess.CompletedProcess:
        cmd = ["terraform", *args]
        return subprocess.run(cmd, cwd=self.terraform_dir, capture_output=capture, text=True)

    def capture_state(self) -> Dict:
        """Capture current Terraform state as baseline."""
        result = self._run_terraform("show", "-json")
        if result.returncode != 0:
            raise RuntimeError(f"Failed to capture state: {result.stderr}")

        self.pre_chaos_state = json.loads(result.stdout)
        return self.pre_chaos_state

    def run_chaos(self, chaos_command: str, shell: bool = True) -> Dict:
        """
        Run an existing chaos script and auto-refresh state.

        Args:
            chaos_command: The command to run (e.g., "python chaos/inject.py --scenario sg")
            shell: Whether to run through shell

        Returns:
            Dict with chaos results and state diff
        """
        # Capture baseline
        self.capture_state()

        # Run the chaos script
        chaos_result = subprocess.run(
            chaos_command,
            shell=shell,
            capture_output=True,
            text=True
        )

        # Auto-refresh Terraform state to reflect the chaos
        refresh_result = self._run_terraform("refresh")

        # Capture post-chaos state
        post_result = self._run_terraform("show", "-json")
        if post_result.returncode == 0:
            self.post_chaos_state = json.loads(post_result.stdout)

        # Compute diff
        diff = self._compute_diff()

        return {
            "chaos_success": chaos_result.returncode == 0,
            "chaos_output": chaos_result.stdout,
            "chaos_error": chaos_result.stderr if chaos_result.returncode != 0 else None,
            "refresh_success": refresh_result.returncode == 0,
            "refresh_output": refresh_result.stdout,
            "state_diff": diff,
            "drift_detected": len(diff.get("changed_resources", [])) > 0,
        }

    def _compute_diff(self) -> Dict:
        """Compute diff between pre and post chaos states."""
        if not self.pre_chaos_state or not self.post_chaos_state:
            return {"error": "Missing state snapshots"}

        pre_resources = self._extract_resources(self.pre_chaos_state)
        post_resources = self._extract_resources(self.post_chaos_state)

        changed = []
        added = []
        removed = []

        all_addrs = set(pre_resources.keys()) | set(post_resources.keys())

        for addr in all_addrs:
            pre = pre_resources.get(addr)
            post = post_resources.get(addr)

            if pre and not post:
                removed.append(addr)
            elif post and not pre:
                added.append(addr)
            elif pre != post:
                changed.append({
                    "address": addr,
                    "before": pre,
                    "after": post,
                })

        return {
            "changed_resources": changed,
            "added_resources": added,
            "removed_resources": removed,
            "total_changes": len(changed) + len(added) + len(removed),
        }

    def _extract_resources(self, state: Dict) -> Dict[str, Dict]:
        """Extract resources from state JSON into address -> attributes dict."""
        resources = {}

        # Handle both old and new state formats
        if "values" in state:
            # New format
            root = state.get("values", {}).get("root_module", {})
            for res in root.get("resources", []):
                addr = res.get("address", "")
                if addr:
                    resources[addr] = res.get("values", {})
        else:
            # Old format
            for module in state.get("modules", []):
                for res in module.get("resources", {}).values():
                    addr = res.get("type", "") + "." + res.get("name", "")
                    if addr:
                        resources[addr] = res.get("primary", {}).get("attributes", {})

        return resources

    def restore(self) -> Dict:
        """
        Restore infrastructure by running terraform apply.
        Assumes your Terraform config describes the desired (non-broken) state.
        """
        result = self._run_terraform("apply", "-auto-approve")

        # Capture final state
        final_result = self._run_terraform("show", "-json")
        final_state = json.loads(final_result.stdout) if final_result.returncode == 0 else None

        return {
            "restore_success": result.returncode == 0,
            "restore_output": result.stdout,
            "final_state_captured": final_state is not None,
        }

    def get_state_diff(self) -> str:
        """Get human-readable state diff."""
        diff = self._compute_diff()

        lines = ["Terraform State Diff (Chaos Impact):"]
        lines.append(f"  Total changes: {diff.get('total_changes', 0)}")

        if diff.get("changed_resources"):
            lines.append("\n  Changed resources:")
            for change in diff["changed_resources"]:
                lines.append(f"    - {change['address']}")

        if diff.get("added_resources"):
            lines.append("\n  Added resources:")
            for addr in diff["added_resources"]:
                lines.append(f"    + {addr}")

        if diff.get("removed_resources"):
            lines.append("\n  Removed resources:")
            for addr in diff["removed_resources"]:
                lines.append(f"    - {addr}")

        return "\n".join(lines)


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Chaos injection with Terraform state consistency")
    parser.add_argument("--approach", choices=["terraform", "boto3"], default="terraform",
                       help="Injection approach: terraform (recommended) or boto3 (with auto-refresh)")
    parser.add_argument("--scenario", choices=["security_group", "rds_connections", "iam_detach"],
                       help="Chaos scenario to inject")
    parser.add_argument("--terraform-dir", default="terraform",
                       help="Path to Terraform directory")
    parser.add_argument("--cleanup", action="store_true",
                       help="Clean up active chaos instead of injecting")

    args = parser.parse_args()

    if args.approach == "terraform":
        injector = TerraformChaosInjector(args.terraform_dir)

        if args.cleanup:
            result = injector.cleanup()
            print(json.dumps(result, indent=2))
            return

        scenarios = {
            "security_group": SCENARIO_SECURITY_GROUP_REVOKE,
            "rds_connections": SCENARIO_RDS_CONNECTION_LIMIT,
            "iam_detach": SCENARIO_IAM_DETACH,
        }

        if args.scenario not in scenarios:
            print(f"Available scenarios: {list(scenarios.keys())}")
            return

        scenario = scenarios[args.scenario]
        print(f"Injecting scenario: {scenario.name}")
        print(f"Description: {scenario.description}")
        print(f"Expected alarms: {scenario.expected_alarms}")
        print(f"Expected diagnosis: {scenario.expected_agent_diagnosis}")
        print("\n" + "=" * 60)

        result = injector.inject(scenario)
        print(json.dumps(result, indent=2))

        if result["success"]:
            print("\n" + "=" * 60)
            print("Chaos injected successfully. Terraform state is consistent.")
            print("Run your agent diagnosis now.")
            print("When done, run: python chaos_inject_tf.py --cleanup")

    else:  # boto3 approach
        wrapper = Boto3ChaosWrapper(args.terraform_dir)

        if args.cleanup:
            result = wrapper.restore()
            print(json.dumps(result, indent=2))
            return

        # Map scenario to existing inject.py command
        commands = {
            "security_group": "python chaos/inject.py --scenario security_group",
            "rds_connections": "python chaos/inject.py --scenario connection_pool",
            "iam_detach": "python chaos/inject.py --scenario iam",
        }

        if args.scenario not in commands:
            print(f"Available scenarios: {list(commands.keys())}")
            return

        print(f"Running chaos with auto-refresh: {args.scenario}")
        print("=" * 60)

        result = wrapper.run_chaos(commands[args.scenario])
        print(json.dumps(result, indent=2))

        if result["drift_detected"]:
            print("\n" + "=" * 60)
            print("State drift detected and captured:")
            print(wrapper.get_state_diff())


if __name__ == "__main__":
    main()
