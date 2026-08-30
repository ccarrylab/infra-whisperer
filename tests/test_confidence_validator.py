"""
Tests for confidence_validator.py.

These validate the deterministic, evidence-based confidence rubric against
both synthetic cases and the project's real historical incidents. The rubric
has been calibrated against 4 real incidents (see confidence_validator.py).

Run with:
    pytest tests/test_confidence_validator.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from confidence_validator import (
    ConfidenceRubric,
    EvidenceFlag,
    Incident,
    HISTORICAL_INCIDENTS,
)


class TestConfidenceRubric:
    def test_no_evidence_scores_zero(self):
        rubric = ConfidenceRubric()
        result = rubric.score([])
        assert result.score == 0.0
        assert result.verdict == "REJECT"
        assert result.requires_human_verification is True

    def test_single_signal_moderate(self):
        rubric = ConfidenceRubric()
        result = rubric.score([EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES])
        assert result.score == 20.0
        assert result.verdict == "LOW"
        assert result.requires_human_verification is True

    def test_multiple_independent_sources_high(self):
        rubric = ConfidenceRubric()
        result = rubric.score([
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.TERRAFORM_STATE_CONFIRMS,
            EvidenceFlag.LOGS_CONFIRM,
        ])
        assert result.score == 65.0
        assert result.verdict == "MODERATE"
        assert result.requires_human_verification is True

    def test_full_independent_signals_high(self):
        rubric = ConfidenceRubric()
        result = rubric.score([
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.TERRAFORM_STATE_CONFIRMS,
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.INDEPENDENT_SECOND_SIGNAL,
        ])
        assert result.score == 80.0
        assert result.verdict == "HIGH"
        assert result.requires_human_verification is False

    def test_score_clamps_at_100(self):
        rubric = ConfidenceRubric()
        result = rubric.score([
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.TERRAFORM_STATE_CONFIRMS,
            EvidenceFlag.LOGS_CONFIRM,
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.INDEPENDENT_SECOND_SIGNAL,
            EvidenceFlag.RECENT_DEPLOYMENT_MATCHES,
        ])
        assert result.score == 100.0
        assert result.score <= 100.0

    def test_contradicting_evidence_pulls_score_down(self):
        rubric = ConfidenceRubric()
        result = rubric.score([
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.CONTRADICTING_EVIDENCE,
        ])
        assert result.score == 0.0
        assert result.verdict == "REJECT"

    def test_temporal_only_rejects(self):
        rubric = ConfidenceRubric()
        result = rubric.score([EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY])
        assert result.score == 0.0
        assert result.verdict == "REJECT"
        assert result.requires_human_verification is True

    def test_temporal_plus_ecs_still_rejects(self):
        """The post-ECS-tool misdiagnosis pattern: real ECS events +
        temporal coincidence, but no actual root cause evidence."""
        rubric = ConfidenceRubric()
        result = rubric.score([
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY,
        ])
        assert result.score == 0.0
        assert result.verdict == "REJECT"


class TestRetroactiveValidationAgainstRealIncidents:
    """Validate the rubric against the project's actual incident history."""

    def test_all_historical_incidents_classify_correctly(self):
        rubric = ConfidenceRubric()
        for incident in HISTORICAL_INCIDENTS:
            result = rubric.score(incident.evidence_present)
            if incident.was_correct_diagnosis:
                assert result.verdict in ["HIGH", "MODERATE", "LOW"], (
                    f"{incident.name}: correct diagnosis should not be REJECT"
                )
            else:
                assert result.verdict in ["REJECT", "LOW"], (
                    f"{incident.name}: wrong diagnosis should be REJECT or LOW, got {result.verdict}"
                )

    def test_security_group_incident_scores_high(self):
        rubric = ConfidenceRubric()
        result = rubric.score([
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.TERRAFORM_STATE_CONFIRMS,
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.INDEPENDENT_SECOND_SIGNAL,
        ])
        assert result.score >= 75.0
        assert result.verdict == "HIGH"

    def test_iam_blind_spot_scores_reject(self):
        """The IAM policy detachment: agent had zero real evidence."""
        rubric = ConfidenceRubric()
        result = rubric.score([EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY])
        assert result.verdict == "REJECT"

    def test_post_ecs_misdiagnosis_scores_reject(self):
        """The post-ECS-tool misdiagnosis: temporal coincidence only."""
        rubric = ConfidenceRubric()
        result = rubric.score([
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY,
        ])
        assert result.verdict == "REJECT"


class TestCalibrationMetrics:
    def test_discrimination_is_positive(self):
        rubric = ConfidenceRubric()
        report = rubric.validate_against_history(HISTORICAL_INCIDENTS)
        assert report["discrimination"] > 0
        assert report["avg_correct_score"] > report["avg_incorrect_score"]

    def test_no_false_positives_at_high_confidence(self):
        rubric = ConfidenceRubric()
        report = rubric.validate_against_history(HISTORICAL_INCIDENTS)
        # No wrong diagnosis should score HIGH
        for r in report["incident_results"]:
            if not r["correct"]:
                assert r["verdict"] != "HIGH", (
                    f"{r['incident']}: wrong diagnosis scored HIGH"
                )
