"""
Tool implementations for Infra Whisperer's agent loop.

Each function here corresponds to a tool definition passed to Claude's
tool-use API. Keep these narrow and single-purpose - the agent's job is to
compose them, not for any one tool to do too much.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone

import boto3
from confidence_scoring import score_evidence

_cloudwatch = None
_logs_client = None


def _get_cloudwatch():
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client("cloudwatch")
    return _cloudwatch


def _get_logs_client():
    global _logs_client
    if _logs_client is None:
        _logs_client = boto3.client("logs")
    return _logs_client


# ---------------------------------------------------------------------------
# Tool: query_cloudwatch
# ---------------------------------------------------------------------------

def query_cloudwatch(alarm_name_prefix: str, lookback_minutes: int = 15) -> dict:
    """Return the state and recent history of alarms matching a prefix.

    This is the agent's "what's on fire" tool - it's the first thing called
    when a scan or webhook indicates an incident may be in progress.
    """
    cw = _get_cloudwatch()
    resp = cw.describe_alarms(AlarmNamePrefix=alarm_name_prefix)
    alarms = []
    for alarm in resp.get("MetricAlarms", []):
        history = cw.describe_alarm_history(
            AlarmName=alarm["AlarmName"],
            HistoryItemType="StateUpdate",
            StartDate=datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes),
            EndDate=datetime.now(timezone.utc),
        )
        alarms.append(
            {
                "name": alarm["AlarmName"],
                "state": alarm["StateValue"],
                "reason": alarm.get("StateReason"),
                "metric": alarm.get("MetricName"),
                "namespace": alarm.get("Namespace"),
                "recent_transitions": [
                    {"timestamp": str(h["Timestamp"]), "summary": h["HistorySummary"]}
                    for h in history.get("AlarmHistoryItems", [])
                ],
            }
        )
    return {"alarms": alarms}


def query_log_group(log_group_name: str, filter_pattern: str = "", lookback_minutes: int = 15) -> dict:
    """Pull recent log lines matching an optional filter pattern.

    Used after query_cloudwatch narrows down which component is unhealthy,
    to pull corroborating evidence (e.g. connection refused errors, IAM
    AccessDenied messages) before proposing a diagnosis.
    """
    start_time = int((datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).timestamp() * 1000)
    resp = _get_logs_client().filter_log_events(
        logGroupName=log_group_name,
        startTime=start_time,
        filterPattern=filter_pattern,
        limit=100,
    )
    return {
        "events": [
            {"timestamp": str(e["timestamp"]), "message": e["message"]}
            for e in resp.get("events", [])
        ]
    }


def query_ecs_service_events(cluster_name: str, service_name: str, max_events: int = 15) -> dict:
    """Return the most recent ECS service events - deployment status, task start/stop
    reasons, and placement failures.

    This is the tool that catches incidents CloudWatch alarms and app logs both miss:
    a rolling deployment can fail repeatedly (bad task definition, missing IAM permission,
    image pull failure) while the OLD tasks keep serving traffic under
    minimumHealthyPercent, so no alarm ever fires and no new logs get written by the
    failing tasks. ECS service events are often the only place this kind of incident is
    visible at all.
    """
    ecs_client = boto3.client("ecs")
    resp = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
    services = resp.get("services", [])
    if not services:
        return {"error": f"service {service_name} not found in cluster {cluster_name}"}

    service = services[0]
    events = service.get("events", [])[:max_events]
    deployments = service.get("deployments", [])

    return {
        "desired_count": service.get("desiredCount"),
        "running_count": service.get("runningCount"),
        "pending_count": service.get("pendingCount"),
        "deployments": [
            {
                "status": d.get("status"),
                "rollout_state": d.get("rolloutState"),
                "rollout_state_reason": d.get("rolloutStateReason"),
                "failed_tasks": d.get("failedTasks"),
                "desired_count": d.get("desiredCount"),
                "running_count": d.get("runningCount"),
            }
            for d in deployments
        ],
        "recent_events": [
            {"timestamp": str(e["createdAt"]), "message": e["message"]}
            for e in events
        ],
    }


# ---------------------------------------------------------------------------
# Tool: read_terraform_state
# ---------------------------------------------------------------------------

def read_terraform_state(terraform_dir: str = "../terraform") -> dict:
    """Return the current Terraform state as JSON.

    The agent cross-references this against the CloudWatch/log evidence to
    find root cause - e.g. "the security group ingress rule for port 5432
    was removed" is visible here even if the symptom (connection timeouts)
    only shows up in the app logs.
    """
    result = subprocess.run(
        ["terraform", "show", "-json"],
        cwd=terraform_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Tool: propose_tf_diff
# ---------------------------------------------------------------------------

def propose_tf_diff(file_path: str, explanation: str, diff: str) -> dict:
    """Record a proposed Terraform change for human review.

    This tool does NOT apply anything - it only stages a diff and an
    explanation. The human-approval gate (open_github_pr + manual merge +
    manual `terraform apply`) is what keeps this safe to run against real
    infrastructure.
    """
    return {
        "file_path": file_path,
        "explanation": explanation,
        "diff": diff,
        "status": "proposed_not_applied",
    }


# ---------------------------------------------------------------------------
# Tool: score_diagnosis_confidence
# ---------------------------------------------------------------------------

def score_diagnosis_confidence(
    alarm_correlates: bool = False,
    terraform_confirms: bool = False,
    logs_confirm: bool = False,
    ecs_events_correlate: bool = False,
    independent_second_signal: bool = False,
    temporal_only: bool = False,
    contradicting_evidence: bool = False,
    no_supporting_evidence: bool = False,
) -> dict:
    """Compute an evidence-based confidence score. This replaces free-form
    confidence percentages with a deterministic score computed from which
    evidence sources actually agree. Call this BEFORE stating a confidence
    level in your final diagnosis, and report the returned percentage and
    breakdown exactly as given - do not substitute your own estimate.
    """
    return score_evidence(
        {
            "alarm_correlates": alarm_correlates,
            "terraform_confirms": terraform_confirms,
            "logs_confirm": logs_confirm,
            "ecs_events_correlate": ecs_events_correlate,
            "independent_second_signal": independent_second_signal,
            "temporal_only": temporal_only,
            "contradicting_evidence": contradicting_evidence,
            "no_supporting_evidence": no_supporting_evidence,
        }
    )


# ---------------------------------------------------------------------------
# Tool: open_github_pr
# ---------------------------------------------------------------------------

def open_github_pr(branch_name: str, title: str, body: str, file_path: str, new_content: str) -> dict:
    """Open a PR with the proposed fix. Requires GITHUB_TOKEN and GITHUB_REPO env vars.

    Kept as a separate, explicit step from propose_tf_diff so the "diagnose"
    and "act" phases are auditable independently - useful both for safety
    and for narrating the flow in an interview demo.
    """
    import os

    from github import Github

    token = os.environ["GITHUB_TOKEN"]
    repo_name = os.environ["GITHUB_REPO"]  # e.g. "ccarrylab/infra-whisperer"

    gh = Github(token)
    repo = gh.get_repo(repo_name)

    source_branch = repo.default_branch
    source = repo.get_branch(source_branch)
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=source.commit.sha)

    contents = repo.get_contents(file_path, ref=source_branch)
    repo.update_file(
        path=file_path,
        message=f"agent: {title}",
        content=new_content,
        sha=contents.sha,
        branch=branch_name,
    )

    pr = repo.create_pull(title=title, body=body, head=branch_name, base=source_branch)
    return {"pr_url": pr.html_url, "pr_number": pr.number}


# ---------------------------------------------------------------------------
# Tool schema definitions passed to the Anthropic API
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "query_cloudwatch",
        "description": "Check the state and recent history of CloudWatch alarms matching a name prefix. Use this first to find out what's currently unhealthy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alarm_name_prefix": {"type": "string"},
                "lookback_minutes": {"type": "integer", "default": 15},
            },
            "required": ["alarm_name_prefix"],
        },
    },
    {
        "name": "query_log_group",
        "description": "Pull recent log lines from a CloudWatch log group, optionally filtered. Use after query_cloudwatch to gather corroborating evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "log_group_name": {"type": "string"},
                "filter_pattern": {"type": "string", "default": ""},
                "lookback_minutes": {"type": "integer", "default": 15},
            },
            "required": ["log_group_name"],
        },
    },
    {
        "name": "query_ecs_service_events",
        "description": "Return recent ECS service events, deployment status, and running/pending/desired task counts. ALWAYS check this in addition to query_cloudwatch and query_log_group - some incidents (deployment failures, IAM permission errors, task placement failures) are invisible to CloudWatch alarms because old healthy tasks keep serving traffic during a failed rolling deployment, and invisible to log tools because the failure is what prevents new logs from being written in the first place. This tool is often the ONLY place such incidents are visible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster_name": {"type": "string"},
                "service_name": {"type": "string"},
                "max_events": {"type": "integer", "default": 15},
            },
            "required": ["cluster_name", "service_name"],
        },
    },
    {
        "name": "read_terraform_state",
        "description": "Return the current Terraform state as JSON, to cross-reference against observed symptoms and find root cause (e.g. a mutated security group rule).",
        "input_schema": {
            "type": "object",
            "properties": {"terraform_dir": {"type": "string", "default": "../terraform"}},
        },
    },
    {
        "name": "propose_tf_diff",
        "description": "Stage a proposed Terraform fix for human review. Does NOT apply the change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "explanation": {"type": "string", "description": "Plain-English, non-technical explanation of the fix"},
                "diff": {"type": "string"},
            },
            "required": ["file_path", "explanation", "diff"],
        },
    },
    {
        "name": "score_diagnosis_confidence",
        "description": "Compute an evidence-based confidence score for a hypothesis. ALWAYS call this before stating a confidence percentage in your final diagnosis - do not invent your own percentage. Set each flag to true only if you have actually observed that evidence in your investigation. Report the returned confidence_percent, supporting_signals, contradicting_signals, and independent_evidence_sources exactly as returned.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alarm_correlates": {"type": "boolean", "description": "A CloudWatch alarm's state/timing directly supports this hypothesis"},
                "terraform_confirms": {"type": "boolean", "description": "Terraform state shows the specific misconfiguration or drift matching this hypothesis"},
                "logs_confirm": {"type": "boolean", "description": "Application or ECS logs contain direct evidence (e.g. an error message) matching this hypothesis"},
                "ecs_events_correlate": {"type": "boolean", "description": "ECS service events show a specific failure reason matching this hypothesis"},
                "independent_second_signal": {"type": "boolean", "description": "At least two of the above are true AND come from genuinely independent sources (not just two views of the same underlying fact)"},
                "temporal_only": {"type": "boolean", "description": "The only evidence is that two events happened close together in time, with no other corroboration - set this honestly even if it feels like it weakens your case"},
                "contradicting_evidence": {"type": "boolean", "description": "Something you observed actively contradicts this hypothesis"},
                "no_supporting_evidence": {"type": "boolean", "description": "You are stating this hypothesis without having found supporting evidence for it specifically"},
            },
        },
    },
    {
        "name": "open_github_pr",
        "description": "Open a GitHub PR containing the proposed fix, so a human can review and merge it before anything is applied. IMPORTANT: file_path must be a path RELATIVE TO THE REPOSITORY ROOT (e.g. 'terraform/modules/rds/main.tf'), never a local filesystem path with '../' - GitHub's API will 404 on '../' prefixed paths since they don't exist in the repo's file tree, even though the same '../' style path is correct for local tools like read_terraform_state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch_name": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "file_path": {"type": "string", "description": "Path relative to the repo root, no '../' prefix - e.g. 'terraform/modules/rds/main.tf'"},
                "new_content": {"type": "string"},
            },
            "required": ["branch_name", "title", "body", "file_path", "new_content"],
        },
    },
]

TOOL_IMPLEMENTATIONS = {
    "query_cloudwatch": query_cloudwatch,
    "query_log_group": query_log_group,
    "query_ecs_service_events": query_ecs_service_events,
    "read_terraform_state": read_terraform_state,
    "propose_tf_diff": propose_tf_diff,
    "score_diagnosis_confidence": score_diagnosis_confidence,
    "open_github_pr": open_github_pr,
}
