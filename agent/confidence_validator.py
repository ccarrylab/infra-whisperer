"""
confidence_validator.py

Validates and tunes the diagnosis confidence rubric against historical incidents.
Replaces free-form model confidence with an evidence-based, auditable, and testable system.

Drop-in: place in agent/ alongside agent.py and tools.py
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
import json


class EvidenceFlag(Enum):
    """Evidence types the agent can honestly report."""
    CLOUDWATCH_ALARM_CORRELATES = "cloudwatch_alarm_correlates"
    TERRAFORM_STATE_CONFIRMS = "terraform_state_confirms"
    LOGS_CONFIRM = "logs_confirm"
    ECS_EVENTS_CORRELATE = "ecs_events_correlate"
    INDEPENDENT_SECOND_SIGNAL = "independent_second_signal"
    TEMPORAL_COINCIDENCE_ONLY = "temporal_coincidence_only"
    CONTRADICTING_EVIDENCE = "contradicting_evidence"
    NO_RECENT_DEPLOYMENT = "no_recent_deployment"
    RECENT_DEPLOYMENT_MATCHES = "recent_deployment_matches"


@dataclass
class Incident:
    """A historical incident for validation."""
    name: str
    description: str
    evidence_present: List[EvidenceFlag]
    was_correct_diagnosis: bool  # Ground truth
    agent_stated_confidence: Optional[float] = None
    notes: str = ""


@dataclass  
class ConfidenceResult:
    """Result of scoring a diagnosis."""
    score: float
    max_possible: float
    percentage: float
    evidence_breakdown: Dict[str, float]
    verdict: str  # "HIGH", "MODERATE", "LOW", "REJECT"
    requires_human_verification: bool
    reasoning: str


class ConfidenceRubric:
    """
    Deterministic, weighted confidence rubric.

    Weights are tunable and validated against historical incidents.
    The agent MUST call this tool before stating any confidence level.
    """

    # Default weights based on README incident analysis
    # These can be tuned via validate_and_tune()
    DEFAULT_WEIGHTS = {
        EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES: 20.0,
        EvidenceFlag.TERRAFORM_STATE_CONFIRMS: 25.0,
        EvidenceFlag.LOGS_CONFIRM: 20.0,
        EvidenceFlag.ECS_EVENTS_CORRELATE: 15.0,
        EvidenceFlag.INDEPENDENT_SECOND_SIGNAL: 20.0,
        EvidenceFlag.RECENT_DEPLOYMENT_MATCHES: 10.0,
        # Penalties
        EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY: -30.0,
        EvidenceFlag.CONTRADICTING_EVIDENCE: -50.0,
        EvidenceFlag.NO_RECENT_DEPLOYMENT: 0.0,  # Neutral
    }

    # Thresholds
    HIGH_CONFIDENCE_MIN = 75.0
    MODERATE_CONFIDENCE_MIN = 50.0
    LOW_CONFIDENCE_MIN = 25.0

    def __init__(self, weights: Optional[Dict[EvidenceFlag, float]] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()

    def score(self, evidence_flags: List[EvidenceFlag]) -> ConfidenceResult:
        """
        Score a diagnosis based on evidence flags.

        Args:
            evidence_flags: List of evidence flags present for this diagnosis.
                            The agent must be honest — false flags are an integrity issue.

        Returns:
            ConfidenceResult with score, verdict, and reasoning.
        """
        score = 0.0
        breakdown = {}

        for flag in evidence_flags:
            weight = self.weights.get(flag, 0.0)
            score += weight
            breakdown[flag.value] = weight

        # Cap at 100, floor at 0
        score = max(0.0, min(100.0, score))

        # Determine verdict
        if score >= self.HIGH_CONFIDENCE_MIN:
            verdict = "HIGH"
            requires_human_verification = False
            reasoning = f"Score {score:.1f} >= {self.HIGH_CONFIDENCE_MIN}. Multiple independent signals confirm root cause."
        elif score >= self.MODERATE_CONFIDENCE_MIN:
            verdict = "MODERATE"
            requires_human_verification = True
            reasoning = f"Score {score:.1f} indicates plausible diagnosis but lacks full independent confirmation. Run `terraform plan` to verify before merge."
        elif score >= self.LOW_CONFIDENCE_MIN:
            verdict = "LOW"
            requires_human_verification = True
            reasoning = f"Score {score:.1f} is weak. Diagnosis relies on limited or circumstantial evidence. Human review mandatory."
        else:
            verdict = "REJECT"
            requires_human_verification = True
            reasoning = f"Score {score:.1f} is below threshold. Insufficient evidence to propose a fix. Investigate further before opening PR."

        return ConfidenceResult(
            score=score,
            max_possible=100.0,
            percentage=score,
            evidence_breakdown=breakdown,
            verdict=verdict,
            requires_human_verification=requires_human_verification,
            reasoning=reasoning
        )

    def validate_against_history(self, incidents: List[Incident]) -> Dict:
        """
        Validate current weights against known historical incidents.

        Returns calibration metrics:
        - discrimination: does score distinguish correct from incorrect?
        - false_positive_rate: high confidence on wrong diagnoses
        - false_negative_rate: low confidence on correct diagnoses
        """
        results = []
        correct_scores = []
        incorrect_scores = []

        for incident in incidents:
            result = self.score(incident.evidence_present)
            results.append({
                "incident": incident.name,
                "correct": incident.was_correct_diagnosis,
                "score": result.score,
                "verdict": result.verdict,
                "agent_claimed": incident.agent_stated_confidence,
            })

            if incident.was_correct_diagnosis:
                correct_scores.append(result.score)
            else:
                incorrect_scores.append(result.score)

        # Calibration metrics
        avg_correct = sum(correct_scores) / len(correct_scores) if correct_scores else 0
        avg_incorrect = sum(incorrect_scores) / len(incorrect_scores) if incorrect_scores else 0

        # Discrimination: correct should score higher than incorrect
        discrimination = avg_correct - avg_incorrect

        # False positives: incorrect diagnoses that scored HIGH
        fp = sum(1 for r in results if not r["correct"] and r["verdict"] == "HIGH")
        fp_rate = fp / len(incorrect_scores) if incorrect_scores else 0

        # False negatives: correct diagnoses that scored below MODERATE
        fn = sum(1 for r in results if r["correct"] and r["verdict"] in ["LOW", "REJECT"])
        fn_rate = fn / len(correct_scores) if correct_scores else 0

        return {
            "incident_results": results,
            "avg_correct_score": avg_correct,
            "avg_incorrect_score": avg_incorrect,
            "discrimination": discrimination,  # Higher is better
            "false_positive_rate": fp_rate,
            "false_negative_rate": fn_rate,
            "calibrated": discrimination > 20 and fp_rate == 0 and fn_rate == 0,
        }

    def tune_weights(self, incidents: List[Incident], 
                     target_discrimination: float = 25.0,
                     max_iterations: int = 1000) -> Dict[EvidenceFlag, float]:
        """
        Simple grid search to find weights that maximize discrimination
        between correct and incorrect diagnoses.

        This is NOT a replacement for human judgment — it finds weights
        that correctly classify your *known* history.
        """
        import random

        best_weights = self.weights.copy()
        best_report = self.validate_against_history(incidents)

        # Only tune positive weights; penalties are fixed by design
        tunable = [
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.TERRAFORM_STATE_CONFIRMS,
            EvidenceFlag.LOGS_CONFIRM,
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.INDEPENDENT_SECOND_SIGNAL,
            EvidenceFlag.RECENT_DEPLOYMENT_MATCHES,
        ]

        for _ in range(max_iterations):
            # Perturb one weight
            candidate = best_weights.copy()
            flag = random.choice(tunable)
            current = candidate[flag]
            # Perturb by +/- 5
            delta = random.choice([-5, 5])
            candidate[flag] = max(5.0, min(40.0, current + delta))

            # Test
            old_weights = self.weights
            self.weights = candidate
            report = self.validate_against_history(incidents)
            self.weights = old_weights

            # Keep if better discrimination and no false positives
            if (report["discrimination"] > best_report["discrimination"] and 
                report["false_positive_rate"] == 0):
                best_weights = candidate
                best_report = report

        return best_weights


# =============================================================================
# HISTORICAL INCIDENTS FROM THIS PROJECT
# =============================================================================

HISTORICAL_INCIDENTS = [
    Incident(
        name="security_group_revoked",
        description="ALB alarm fired, ECS events showed task churn, Terraform state showed missing SG rule. Agent diagnosed correctly.",
        evidence_present=[
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.TERRAFORM_STATE_CONFIRMS,
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.INDEPENDENT_SECOND_SIGNAL,
        ],
        was_correct_diagnosis=True,
        agent_stated_confidence=85.0,
        notes="The gold standard: multiple independent signals all pointed to the same root cause."
    ),
    Incident(
        name="unhealthy_host_count_silent",
        description="Discovered that UnHealthyHostCount goes to INSUFFICIENT_DATA on fully-drained target group. Added HealthyHostCount alarm.",
        evidence_present=[
            EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
            EvidenceFlag.INDEPENDENT_SECOND_SIGNAL,
        ],
        was_correct_diagnosis=True,
        agent_stated_confidence=None,
        notes="Finding from live testing, not an agent diagnosis. Included for rubric validation."
    ),
    Incident(
        name="iam_policy_detachment",
        description="IAM policy detached. New tasks failed with AccessDeniedException. Old tasks kept serving traffic. Zero CloudWatch alarms. Agent re-diagnosed max_connections.",
        evidence_present=[
            EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY,
            # The agent had NO real evidence but stated confidence anyway
        ],
        was_correct_diagnosis=False,
        agent_stated_confidence=70.0,  # Implied high confidence in max_connections
        notes="The original blind spot: agent had zero actual evidence but constructed a confident wrong answer from absence of contradicting signals."
    ),
    Incident(
        name="post_ecs_tool_misdiagnosis",
        description="After adding ECS-events tool, agent saw task churn from forced redeployment and wove it into incorrect max_connections narrative. No DB error logs, no connections alarm.",
        evidence_present=[
            EvidenceFlag.ECS_EVENTS_CORRELATE,
            EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY,
            # No terraform confirmation, no logs, no alarm correlation
        ],
        was_correct_diagnosis=False,
        agent_stated_confidence=65.0,
        notes="More tooling = more raw material for confident wrong answers. Temporal proximity != causation."
    ),
]


def main():
    """Run validation against project history and optionally tune weights."""
    print("=" * 70)
    print("CONFIDENCE RUBRIC VALIDATION")
    print("=" * 70)

    rubric = ConfidenceRubric()

    # Validate default weights
    print("\n--- Default Weights ---")
    for flag, weight in rubric.DEFAULT_WEIGHTS.items():
        print(f"  {flag.value}: {weight:+.1f}")

    report = rubric.validate_against_history(HISTORICAL_INCIDENTS)

    print("\n--- Incident Results ---")
    for r in report["incident_results"]:
        status = "✓ CORRECT" if r["correct"] else "✗ WRONG"
        claimed = f"(agent claimed {r['agent_claimed']:.0f}%)" if r["agent_claimed"] else ""
        print(f"  {r['incident']:30s} | Score: {r['score']:5.1f} | {r['verdict']:8s} | {status} {claimed}")

    print(f"\n--- Calibration Metrics ---")
    print(f"  Avg correct score:   {report['avg_correct_score']:.1f}")
    print(f"  Avg incorrect score: {report['avg_incorrect_score']:.1f}")
    print(f"  Discrimination:      {report['discrimination']:.1f} (target: >20)")
    print(f"  False positive rate: {report['false_positive_rate']:.2%}")
    print(f"  False negative rate: {report['false_negative_rate']:.2%}")
    print(f"  Calibrated:          {report['calibrated']}")

    # Tune weights
    print("\n--- Tuning Weights ---")
    tuned = rubric.tune_weights(HISTORICAL_INCIDENTS)
    rubric.weights = tuned
    tuned_report = rubric.validate_against_history(HISTORICAL_INCIDENTS)

    print("\nTuned weights:")
    for flag, weight in tuned.items():
        if weight != ConfidenceRubric.DEFAULT_WEIGHTS.get(flag, 0):
            old = ConfidenceRubric.DEFAULT_WEIGHTS.get(flag, 0)
            print(f"  {flag.value}: {old:.1f} -> {weight:.1f} *")
        else:
            print(f"  {flag.value}: {weight:.1f}")

    print(f"\nTuned discrimination: {tuned_report['discrimination']:.1f}")
    print(f"Tuned calibrated: {tuned_report['calibrated']}")

    # Example usage for agent integration
    print("\n" + "=" * 70)
    print("EXAMPLE: How the agent calls this in practice")
    print("=" * 70)

    # Simulate the security group incident
    sg_evidence = [
        EvidenceFlag.CLOUDWATCH_ALARM_CORRELATES,
        EvidenceFlag.TERRAFORM_STATE_CONFIRMS,
        EvidenceFlag.ECS_EVENTS_CORRELATE,
        EvidenceFlag.INDEPENDENT_SECOND_SIGNAL,
    ]
    result = rubric.score(sg_evidence)
    print(f"\nSecurity-group incident evidence: {[e.value for e in sg_evidence]}")
    print(f"Score: {result.score:.1f}% | Verdict: {result.verdict}")
    print(f"Requires human verification: {result.requires_human_verification}")
    print(f"Reasoning: {result.reasoning}")

    # Simulate the post-ECS misdiagnosis
    bad_evidence = [
        EvidenceFlag.ECS_EVENTS_CORRELATE,
        EvidenceFlag.TEMPORAL_COINCIDENCE_ONLY,
    ]
    result = rubric.score(bad_evidence)
    print(f"\nPost-ECS misdiagnosis evidence: {[e.value for e in bad_evidence]}")
    print(f"Score: {result.score:.1f}% | Verdict: {result.verdict}")
    print(f"Requires human verification: {result.requires_human_verification}")
    print(f"Reasoning: {result.reasoning}")


if __name__ == "__main__":
    main()
