"""
Unit tests for Infra Whisperer's agent tools (updated for confidence_validator
and tf_state_parser integration).

These mock boto3/subprocess entirely - no AWS credentials or real
infrastructure required.

Run with:
    pytest tests/test_tools.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

import tools  # noqa: E402


class TestQueryCloudwatch:
    def test_returns_alarm_list_with_expected_fields(self):
        mock_cloudwatch = MagicMock()
        mock_cloudwatch.describe_alarms.return_value = {
            "MetricAlarms": [
                {
                    "AlarmName": "infra-whisperer-unhealthy-targets",
                    "StateValue": "OK",
                    "StateReason": "Threshold not breached",
                    "MetricName": "UnHealthyHostCount",
                    "Namespace": "AWS/ApplicationELB",
                }
            ]
        }
        mock_cloudwatch.describe_alarm_history.return_value = {"AlarmHistoryItems": []}

        with patch.object(tools, "_get_cloudwatch", return_value=mock_cloudwatch):
            result = tools.query_cloudwatch("infra-whisperer")

        assert len(result["alarms"]) == 1
        alarm = result["alarms"][0]
        assert alarm["name"] == "infra-whisperer-unhealthy-targets"
        assert alarm["state"] == "OK"
        assert alarm["metric"] == "UnHealthyHostCount"

    def test_handles_zero_alarms_gracefully(self):
        mock_cloudwatch = MagicMock()
        mock_cloudwatch.describe_alarms.return_value = {"MetricAlarms": []}

        with patch.object(tools, "_get_cloudwatch", return_value=mock_cloudwatch):
            result = tools.query_cloudwatch("nonexistent-prefix")

        assert result == {"alarms": []}


class TestQueryLogGroup:
    def test_returns_formatted_events(self):
        mock_logs = MagicMock()
        mock_logs.filter_log_events.return_value = {
            "events": [
                {"timestamp": 1700000000000, "message": "HTTP 200 OK"},
                {"timestamp": 1700000001000, "message": "HTTP 200 OK"},
            ]
        }

        with patch.object(tools, "_get_logs_client", return_value=mock_logs):
            result = tools.query_log_group("/ecs/infra-whisperer")

        assert len(result["events"]) == 2
        assert result["events"][0]["message"] == "HTTP 200 OK"


class TestQueryEcsServiceEvents:
    def test_returns_error_when_service_not_found(self):
        mock_ecs = MagicMock()
        mock_ecs.describe_services.return_value = {"services": []}

        with patch("boto3.client", return_value=mock_ecs):
            result = tools.query_ecs_service_events("some-cluster", "missing-service")

        assert "error" in result

    def test_returns_deployment_and_event_summary(self):
        mock_ecs = MagicMock()
        mock_ecs.describe_services.return_value = {
            "services": [
                {
                    "desiredCount": 2,
                    "runningCount": 2,
                    "pendingCount": 0,
                    "deployments": [
                        {
                            "status": "PRIMARY",
                            "rolloutState": "COMPLETED",
                            "rolloutStateReason": "deployment completed",
                            "failedTasks": 0,
                            "desiredCount": 2,
                            "runningCount": 2,
                        }
                    ],
                    "events": [
                        {"createdAt": "2026-08-24T13:18:07", "message": "has reached a steady state."},
                    ],
                }
            ]
        }

        with patch("boto3.client", return_value=mock_ecs):
            result = tools.query_ecs_service_events("infra-whisperer-cluster", "infra-whisperer-service")

        assert result["desired_count"] == 2
        assert result["running_count"] == 2
        assert result["deployments"][0]["rollout_state"] == "COMPLETED"
        assert len(result["recent_events"]) == 1


class TestReadTerraformState:
    def test_parses_terraform_show_output(self):
        fake_state = {"format_version": "1.0", "values": {"root_module": {"resources": []}}}
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(fake_state)

        with patch("subprocess.run", return_value=mock_result):
            result = tools.read_terraform_state(terraform_dir="../terraform")

        assert result["format_version"] == "1.0"


class TestAnalyzeSecurityGroup:
    """Tests for the new semantic security group analyzer."""

    def test_finds_external_rule_when_inline_is_empty(self):
        """The key blind-spot fix: a rule defined as aws_security_group_rule
        should be found even when aws_security_group.ingress is empty."""
        mock_state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs_service",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-12345678",
                            "name": "ecs-service-sg",
                            "ingress": [],
                            "egress": [{"protocol": "-1", "from_port": 0, "to_port": 0, "cidr_blocks": ["0.0.0.0/0"]}]
                        },
                        "dependencies": []
                    }]
                },
                {
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "name": "alb_ingress",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sgrule-123456",
                            "security_group_id": "sg-12345678",
                            "type": "ingress",
                            "protocol": "tcp",
                            "from_port": 80,
                            "to_port": 80,
                            "cidr_blocks": ["0.0.0.0/0"],
                        },
                        "dependencies": ["aws_security_group.ecs_service"]
                    }]
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(mock_state)

        with patch("subprocess.run", return_value=mock_result):
            result = tools.analyze_security_group("aws_security_group.ecs_service")

        assert result["effective_ingress_count"] == 1
        assert result["inline_ingress_count"] == 0
        assert result["external_ingress_count"] == 1
        assert "external: aws_security_group_rule.alb_ingress" in result["report"]

    def test_reports_genuine_drift_when_rule_missing(self):
        """When the rule is actually gone (not just refactored), drift is detected."""
        mock_state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs_service",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-12345678",
                            "name": "ecs-service-sg",
                            "ingress": [],
                            "egress": [{"protocol": "-1", "from_port": 0, "to_port": 0, "cidr_blocks": ["0.0.0.0/0"]}]
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(mock_state)

        expected_rules = [{
            "security_group_address": "aws_security_group.ecs_service",
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": ["0.0.0.0/0"]
        }]

        with patch("subprocess.run", return_value=mock_result):
            result = tools.analyze_security_group(
                "aws_security_group.ecs_service",
                expected_rules=expected_rules
            )

        assert result["drift_detected"] is True
        assert len(result["drift_details"]) == 1
        assert result["drift_details"][0]["type"] == "missing_ingress_rule"

    def test_returns_error_for_missing_security_group(self):
        mock_state = {"version": 4, "terraform_version": "1.5.0", "resources": []}
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(mock_state)

        with patch("subprocess.run", return_value=mock_result):
            result = tools.analyze_security_group("aws_security_group.nonexistent")

        assert "error" in result


class TestScoreDiagnosisConfidence:
    """Tests for the enhanced confidence scoring with verdict system."""

    def test_high_confidence_verdict(self):
        result = tools.score_diagnosis_confidence(
            alarm_correlates=True,
            terraform_confirms=True,
            ecs_events_correlate=True,
            independent_second_signal=True,
        )
        assert result["confidence_percent"] == 80.0
        assert result["verdict"] == "HIGH"
        assert result["requires_human_verification"] is False

    def test_moderate_confidence_verdict(self):
        result = tools.score_diagnosis_confidence(
            alarm_correlates=True,
            terraform_confirms=True,
            logs_confirm=True,
        )
        assert result["verdict"] == "MODERATE"
        assert result["requires_human_verification"] is True
        assert "terraform plan" in result["reasoning"].lower()

    def test_reject_verdict_for_temporal_only(self):
        result = tools.score_diagnosis_confidence(
            ecs_events_correlate=True,
            temporal_only=True,
        )
        assert result["verdict"] == "REJECT"
        assert result["requires_human_verification"] is True
        assert result["confidence_percent"] == 0.0

    def test_contradicting_evidence_rejects(self):
        result = tools.score_diagnosis_confidence(
            alarm_correlates=True,
            contradicting_evidence=True,
        )
        assert result["verdict"] == "REJECT"
        assert result["confidence_percent"] == 0.0

    def test_no_evidence_rejects(self):
        result = tools.score_diagnosis_confidence()
        assert result["verdict"] == "REJECT"
        assert result["confidence_percent"] == 0.0


class TestProposeTfDiff:
    def test_never_applies_only_stages(self):
        result = tools.propose_tf_diff(
            file_path="terraform/modules/rds/main.tf",
            explanation="Raise max_connections to prevent exhaustion",
            diff="- value = \"20\"\n+ value = \"100\"",
        )
        assert result["status"] == "proposed_not_applied"
        assert result["file_path"] == "terraform/modules/rds/main.tf"


class TestToolSchemaConsistency:
    def test_every_defined_tool_has_an_implementation(self):
        defined_names = {t["name"] for t in tools.TOOL_DEFINITIONS}
        implemented_names = set(tools.TOOL_IMPLEMENTATIONS.keys())
        assert defined_names == implemented_names

    def test_new_tools_are_present(self):
        """Verify the enhanced tools are registered."""
        names = {t["name"] for t in tools.TOOL_DEFINITIONS}
        assert "analyze_security_group" in names
        assert "score_diagnosis_confidence" in names
        assert "read_terraform_state" in names

    def test_analyze_security_group_has_expected_schema(self):
        tool = next(t for t in tools.TOOL_DEFINITIONS if t["name"] == "analyze_security_group")
        assert "sg_address" in tool["input_schema"]["properties"]
        assert "expected_rules" in tool["input_schema"]["properties"]
        assert "terraform_dir" in tool["input_schema"]["properties"]

    def test_score_diagnosis_confidence_has_verdict_fields_in_description(self):
        tool = next(t for t in tools.TOOL_DEFINITIONS if t["name"] == "score_diagnosis_confidence")
        assert "verdict" in tool["description"].lower()
        assert "reject" in tool["description"].lower()
