"""
Evidence-based confidence scoring.

This replaces free-form "I'm 85% confident" statements with a deterministic,
auditable score computed from which evidence sources actually support a
hypothesis. The agent's confidence audit (see README) found that Claude's
self-reported confidence did not distinguish correct diagnoses from wrong
ones across four real incidents. This module is the direct fix: confidence
becomes a function of evidence, not a number the model picks because it
sounds right.

The weights below are a starting rubric, not a calibrated model - there is
no training data to calibrate against yet, and this file says so rather than
pretending otherwise. What it does guarantee: the SAME evidence pattern
always produces the SAME score, and the score is fully explainable after
the fact (see `breakdown` in the return value).
"""

WEIGHTS = {
    "alarm_correlates": 30,
    "terraform_confirms": 25,
    "logs_confirm": 25,
    "ecs_events_correlate": 15,
    "independent_second_signal": 20,
    "temporal_only": 5,
    "contradicting_evidence": -30,
    "no_supporting_evidence": -20,
}


def score_evidence(evidence: dict) -> dict:
    """Compute a confidence score from a dict of evidence flags (bool).

    Expected keys (all optional, default False): alarm_correlates,
    terraform_confirms, logs_confirm, ecs_events_correlate,
    independent_second_signal, temporal_only, contradicting_evidence,
    no_supporting_evidence.

    Returns a dict with the clamped confidence percentage, counts of
    supporting/contradicting signals, and a per-flag breakdown so the score
    is auditable rather than a black box.
    """
    breakdown = {}
    raw_score = 0
    supporting_signals = 0
    contradicting_signals = 0

    primary_sources = [
        "alarm_correlates",
        "terraform_confirms",
        "logs_confirm",
        "ecs_events_correlate",
    ]
    independent_sources_used = sum(1 for k in primary_sources if evidence.get(k))

    for key, weight in WEIGHTS.items():
        if evidence.get(key):
            raw_score += weight
            breakdown[key] = weight
            if weight > 0 and key != "independent_second_signal":
                supporting_signals += 1
            elif weight < 0:
                contradicting_signals += 1

    confidence_percent = max(0, min(100, raw_score))

    return {
        "confidence_percent": confidence_percent,
        "supporting_signals": supporting_signals,
        "contradicting_signals": contradicting_signals,
        "independent_evidence_sources": independent_sources_used,
        "breakdown": breakdown,
        "note": "Rubric-based score from a small, uncalibrated set of weights - "
        "not a statistically validated probability. Treat as a structured, "
        "auditable estimate, not ground truth.",
    }
