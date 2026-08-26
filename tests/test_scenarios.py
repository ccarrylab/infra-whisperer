"""
Unit tests for chaos injection scenarios.

Mocked entirely - these tests verify the logic (which rule gets revoked, which
policy gets detached) without touching real AWS. The actual chaos scenarios
were validated for real against live infrastructure - see the README's "found
via live testing" sections. This suite just protects against regressions in
the injection logic itself.

Note: exhaust_connection_pool is intentionally not unit tested here. It opens
real psycopg2 connections and blocks in an interactive while-loop waiting for
a KeyboardInterrupt, which makes it an integration/demo tool rather than a
unit-testable function as written. Testing it properly would mean refactoring
it to accept an injectable stop condition - a reasonable future improvement,
not done here because the scenario itself was never run live during testing
(it needs temporary public RDS access, a tradeoff deliberately not taken -
see the README).

Run with:
    pytest tests/test_scenarios.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "chaos"))

import scenarios  # noqa: E402


class TestBreakSecurityGroup:
    def test_revokes_first_ingress_rule_found(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_security_group_rules.return_value = {
            "SecurityGroupRules": [
                {"SecurityGroupRuleId": "sgr-egress1", "IsEgress": True},
                {"SecurityGroupRuleId": "sgr-ingress1", "IsEgress": False},
                {"SecurityGroupRuleId": "sgr-ingress2", "IsEgress": False},
            ]
        }

        with patch.object(scenarios, "ec2", mock_ec2):
            result = scenarios.break_security_group("sg-test123")

        mock_ec2.revoke_security_group_ingress.assert_called_once_with(
            GroupId="sg-test123", SecurityGroupRuleIds=["sgr-ingress1"]
        )
        assert result["status"] == "ingress_rule_revoked"
        assert result["rule_ids"] == ["sgr-ingress1"]

    def test_returns_status_when_no_ingress_rules_exist(self):
        mock_ec2 = MagicMock()
        mock_ec2.describe_security_group_rules.return_value = {
            "SecurityGroupRules": [
                {"SecurityGroupRuleId": "sgr-egress1", "IsEgress": True},
            ]
        }

        with patch.object(scenarios, "ec2", mock_ec2):
            result = scenarios.break_security_group("sg-test123")

        mock_ec2.revoke_security_group_ingress.assert_not_called()
        assert result["status"] == "no_ingress_rules_found"


class TestBreakIamRole:
    def test_detaches_the_specified_policy(self):
        mock_iam = MagicMock()

        with patch("boto3.client", return_value=mock_iam):
            result = scenarios.break_iam_role(
                "infra-whisperer-ecs-execution-role",
                "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
            )

        mock_iam.detach_role_policy.assert_called_once_with(
            RoleName="infra-whisperer-ecs-execution-role",
            PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )
        assert result["status"] == "policy_detached"
        assert result["role_name"] == "infra-whisperer-ecs-execution-role"
