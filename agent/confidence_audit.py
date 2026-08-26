"""
Confidence calibration audit: the agent reviews every confidence percentage it
stated across this project's real incidents (documented in the README) and
grades itself against what actually turned out to be true.

This is not a mocked exercise - every data point being reviewed is a real
diagnosis from a real incident in this repo's history. The question being
asked is uncomfortable on purpose: when this agent says "85% confident," does
that number mean anything, or is it just a plausible-sounding number attached
to confident-sounding prose?

Run with:
    export ANTHROPIC_API_KEY=...
    python agent/confidence_audit.py
"""

import os
from datetime import datetime, timezone

import anthropic

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are Infra Whisperer. You are being asked to audit your own
confidence calibration using the real incident record in the README you're about to read.

Go through the documented incidents and PRs one at a time. For each one where you (the
agent) stated a confidence percentage, classify it as:
- CONFIRMED CORRECT: the stated root cause was independently verified true (cite the
  verification method documented in the README - e.g. a terraform plan diff, a live
  recovery, an alarm state)
- CONFIRMED WRONG: the stated root cause was later shown to be incorrect or misdirected
  (this happened at least once in this project's real history - find it)
- UNVERIFIED: no independent confirmation is documented either way

Then answer directly: across this sample, do your stated confidence percentages actually
track correctness, or are they decorative? Be honest about the sample size being small and
what that does and doesn't let you conclude. Do not round up your own calibration to sound
better than the actual record supports. If the honest answer is "this sample is too small
to say anything statistically meaningful, but here's the one pattern I can point to," say
exactly that.

Keep it under 5 short paragraphs. Cite specifics from the README, not generic statements.
"""


def main():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    with open("../README.md", "r", encoding="utf-8") as f:
        readme_content = f.read()

    response = client.messages.create(
        model=MODEL,
        max_tokens=1536,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Here is the real incident record. Audit your own calibration:\n\n{readme_content}",
            }
        ],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    output = f"""## Confidence calibration audit

*The agent was given its own real incident record from this README and asked to grade its
own stated confidence percentages against what was actually verified true. Not mocked -
every incident referenced below is real, documented above. Unedited. Generated {timestamp}.*

> {text.replace(chr(10), chr(10) + "> ")}

"""

    print(output)
    with open("/tmp/confidence_audit_output.md", "w", encoding="utf-8") as f:
        f.write(output)
    print("\n\nSaved to /tmp/confidence_audit_output.md")


if __name__ == "__main__":
    main()
