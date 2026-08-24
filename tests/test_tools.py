"""
Unit tests for Infra Whisperer's agent tools.

These mock boto3/subprocess entirely - no AWS credentials or real
infrastructure required to run this suite. That's deliberate: fast,
deterministic tests you can run in CI, separate from the live end-to-end
testing documented in the README (which does hit real AWS and is what
actually validated this project - see the README's "found via live
testing" sections for that).

Run with:
    cd agent
    pip install pytest
    pytest ../tests/test_tools.py -v
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

        with patch.object(tools, "cloudwatch", mock_cloudwatch):
            result = tools.query_cloudwatch("infra-whisperer")

        assert len(result["alarms"]) == 1
        alarm = result["alarms"][0]
        assert alarm["name"] == "infra-whisperer-unhealthy-targets"
        assert alarm["state"] == "OK"
        assert alarm["metric"] == "UnHealthyHostCount"

    def test_handles_zero_alarms_gracefully(self):
        mock_cloudwatch = MagicMock()
        mock_cloudwatch.describe_alarms.return_value = {"MetricAlarms": []}

        with patch.object(tools, "cloudwatch", mock_cloudwatch):
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

        with patch.object(tools, "logs_client", mock_logs):
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


class TestProposeTfDiff:
    def test_never_applies_only_stages(self):
        """The most important property of this tool: it must be a pure
        function with zero side effects. This is the human-approval gate's
        foundation - verify it stays that way."""
        result = tools.propose_tf_diff(
            file_path="terraform/modules/rds/main.tf",
            explanation="Raise max_connections to prevent exhaustion",
            diff="- value = \"20\"\n+ value = \"100\"",
        )
        assert result["status"] == "proposed_not_applied"
        assert result["file_path"] == "terraform/modules/rds/main.tf"


class TestToolSchemaConsistency:
    def test_every_defined_tool_has_an_implementation(self):
        """Regression test for a real bug found during manual testing:
        query_ecs_service_events was added to TOOL_DEFINITIONS but
        forgotten in TOOL_IMPLEMENTATIONS, which would have caused a
        KeyError the first time the agent tried to call it."""
        defined_names = {t["name"] for t in tools.TOOL_DEFINITIONS}
        implemented_names = set(tools.TOOL_IMPLEMENTATIONS.keys())
        assert defined_names == implemented_names

    def test_every_tool_definition_has_required_schema_fields(self):
        for tool in tools.TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"
