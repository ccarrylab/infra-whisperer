"""
Infra Whisperer — main agent loop.

Usage:
    python agent.py --watch                # poll for incidents continuously
    python agent.py --diagnose-now          # run one diagnosis pass immediately

The agent:
  1. Polls CloudWatch alarms for the project (query_cloudwatch)
  2. On an ALARM state, pulls corroborating logs (query_log_group)
  3. Reads Terraform state to cross-reference infra config (read_terraform_state)
  4. Asks Claude to produce a confidence-ranked list of root-cause hypotheses
     and a plain-English explanation
  5. Stages a Terraform diff for the top hypothesis (propose_tf_diff)
  6. Opens a PR for human review (open_github_pr) — nothing is ever applied
     automatically
"""

import argparse
import json
import os
import time

import anthropic

from tools import TOOL_DEFINITIONS, TOOL_IMPLEMENTATIONS

MODEL = "claude-sonnet-4-6"
PROJECT_ALARM_PREFIX = os.environ.get("PROJECT_NAME", "infra-whisperer")

SYSTEM_PROMPT = """You are Infra Whisperer, an incident-diagnosis agent for a
Terraform-managed AWS environment. When invoked, you must:

1. Check CloudWatch alarms to identify what's unhealthy.
2. Pull relevant logs to corroborate the symptom.
3. Read Terraform state to find infrastructure-level root causes.
4. Produce a confidence-ranked list of root-cause hypotheses (not just one
   guess) — rank them the way a senior engineer would, based on the strength
   of the evidence for each.
5. Write a plain-English explanation of the most likely root cause, suitable
   for a non-technical stakeholder (a VP, not an engineer).
6. Propose a minimal, safe Terraform diff that fixes the top hypothesis.
7. Open a GitHub PR with that diff. Do NOT apply anything yourself — a human
   must review and merge before `terraform apply` runs.

Be explicit about your confidence level and what evidence would raise or
lower it. If evidence is ambiguous, say so rather than overstating certainty.
"""


def run_agent_turn(client: anthropic.Anthropic, user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            # Final text response — the diagnosis/summary
            return "".join(block.text for block in response.content if block.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            impl = TOOL_IMPLEMENTATIONS[block.name]
            try:
                result = impl(**block.input)
            except Exception as exc:  # surface tool errors back to the model
                result = {"error": str(exc)}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )

        messages.append({"role": "user", "content": tool_results})


def diagnose_now(client: anthropic.Anthropic) -> None:
    prompt = (
        f"An alarm may have fired for the '{PROJECT_ALARM_PREFIX}' project. "
        "Investigate using your tools, produce a confidence-ranked diagnosis, "
        "and if you find a clear root cause, propose a fix and open a PR."
    )
    result = run_agent_turn(client, prompt)
    print("\n=== Infra Whisperer Diagnosis ===\n")
    print(result)


def watch(client: anthropic.Anthropic, poll_seconds: int = 30) -> None:
    from tools import query_cloudwatch

    print(f"Watching alarms with prefix '{PROJECT_ALARM_PREFIX}'... (Ctrl+C to stop)")
    seen_in_alarm = set()

    while True:
        state = query_cloudwatch(PROJECT_ALARM_PREFIX)
        for alarm in state["alarms"]:
            if alarm["state"] == "ALARM" and alarm["name"] not in seen_in_alarm:
                seen_in_alarm.add(alarm["name"])
                print(f"\n[!] Incident detected: {alarm['name']}")
                diagnose_now(client)
            elif alarm["state"] != "ALARM" and alarm["name"] in seen_in_alarm:
                seen_in_alarm.discard(alarm["name"])
                print(f"\n[ok] Recovered: {alarm['name']}")

        time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watch", action="store_true")
    group.add_argument("--diagnose-now", action="store_true")
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if args.watch:
        watch(client)
    else:
        diagnose_now(client)


if __name__ == "__main__":
    main()
