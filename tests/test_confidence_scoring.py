"""
Tests for confidence_scoring.py.

The last two tests are the important ones: they reconstruct the actual
evidence pattern from two real incidents in this project's history - one
correct diagnosis, one confirmed wrong (the post-ECS-tool misdiagnosis
documented in the README) - and verify the new scoring system would have
produced a low score for the wrong one, where the agent's old free-form
confidence ("~85%") did not distinguish it from the correct diagnosis at all.
This is retroactive validation, not a live re-run - it demonstrates the fix
addresses the actual documented failure, using the real evidence pattern
from that incident.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from confidence_scoring import score_evidence  # noqa: E402


class TestScoreEvidence:
    def test_no_evidence_scores_zero(self):
        result = score_evidence({})
        assert result["confidence_percent"] == 0
        assert result["supporting_signals"] == 0

    def test_single_strong_signal(self):
        result = score_evidence({"alarm_correlates": True})
        assert result["confidence_percent"] == 30
        assert result["supporting_signals"] == 1

    def test_multiple_independent_sources_score_higher(self):
        result = score_evidence(
            {
                "alarm_correlates": True,
                "terraform_confirms": True,
                "logs_confirm": True,
            }
        )
        assert result["confidence_percent"] == 80
        assert result["independent_evidence_sources"] == 3

    def test_score_clamps_at_100(self):
        result = score_evidence(
            {
                "alarm_correlates": True,
                "terraform_confirms": True,
                "logs_confirm": True,
                "ecs_events_correlate": True,
                "independent_second_signal": True,
            }
        )
        assert result["confidence_percent"] == 100

    def test_contradicting_evidence_pulls_score_down(self):
        result = score_evidence(
            {
                "alarm_correlates": True,
                "contradicting_evidence": True,
            }
        )
        assert result["confidence_percent"] == 0
        assert result["contradicting_signals"] == 1

    def test_score_never_goes_negative(self):
        result = score_evidence(
            {
                "contradicting_evidence": True,
                "no_supporting_evidence": True,
            }
        )
        assert result["confidence_percent"] == 0

    def test_temporal_correlation_alone_scores_low(self):
        result = score_evidence({"temporal_only": True})
        assert result["confidence_percent"] == 5
        assert result["supporting_signals"] == 1


class TestRetroactiveValidationAgainstRealIncidents:
    def test_real_security_group_incident_scores_high(self):
        result = score_evidence(
            {
                "alarm_correlates": True,
                "terraform_confirms": True,
                "ecs_events_correlate": True,
                "independent_second_signal": True,
            }
        )
        assert result["confidence_percent"] >= 80
        assert result["independent_evidence_sources"] >= 3

    def test_real_post_ecs_tool_misdiagnosis_scores_low(self):
        result = score_evidence({"temporal_only": True})
        high_confidence_result = score_evidence(
            {
                "alarm_correlates": True,
                "terraform_confirms": True,
                "ecs_events_correlate": True,
            }
        )
        assert result["confidence_percent"] < 20
        assert result["confidence_percent"] < high_confidence_result["confidence_percent"]
