# Infra Whisperer

![Tests](https://github.com/ccarrylab/infra-whisperer/actions/workflows/tests.yml/badge.svg)


I built this to give myself a real answer to "show me you can do FDE work" instead of a
chatbot demo. It's an agent that watches a live AWS environment, figures out why something
broke by cross-referencing CloudWatch, logs, and Terraform state, explains the diagnosis in
plain English, and proposes a fix as a PR — a human still has to merge it and run
`terraform apply`. Nothing gets changed automatically.

I used Claude to help scaffold the Terraform modules and the agent's tool-calling loop
faster than I'd have written it by hand. The architecture decisions — the human-approval
gate, the confidence-ranked diagnosis instead of a single guess, which failure scenarios to
simulate, how tight to make the security group and connection limits so the chaos scripts
have something real to break — are mine. I think that split is honestly a decent proxy for
what FDE work looks like now: knowing how to direct an AI agent well, and knowing which
decisions you don't hand off to it.

## The problem

When production breaks, engineers burn hours correlating CloudWatch alarms, logs, IAM
policies, and Terraform state to find root cause, then manually write and apply the fix.
That's expensive, slow, and error-prone — and it's exactly the kind of cost a Forward
Deployed Engineer gets hired to eliminate for a client.

## What makes this different from a chatbot-wrapper demo

- **Confidence-ranked diagnosis, not a single guess.** The agent proposes multiple root-cause
  hypotheses ranked by likelihood, the way a senior engineer actually reasons through an
  incident.
- **Human-approval gate before any change is applied.** The agent opens a PR with a Terraform
  diff and a plain-English explanation; a human merges it. This is what makes the system
  something a real client would trust near their infrastructure — safe, auditable automation,
  not an agent with `apply` access and no brakes.
- **VP-readable explanations.** Every diagnosis includes a non-technical summary, because the
  hardest part of the FDE job isn't finding root cause — it's explaining it to someone who
  isn't an engineer.
- **Cost-capped by design.** A hard AWS Budget alarm and a one-command teardown mean this can
  run entirely on free/trial credit without risk of a surprise bill.

## Architecture

```
                        ┌─────────────────────┐
   chaos/inject.py ───► │   AWS (Terraform)    │
   (breaks something)   │  ALB → ECS → RDS      │
                        │  + CloudWatch alarms  │
                        └──────────┬────────────┘
                                   │ metrics/logs
                                   ▼
                        ┌─────────────────────┐
                        │   agent/agent.py      │
                        │  (Claude, tool-use)   │
                        │  1. query_cloudwatch  │
                        │  2. read_tf_state     │
                        │  3. rank hypotheses   │
                        │  4. propose_tf_diff   │
                        │  5. open_github_pr    │
                        └──────────┬────────────┘
                                   │
                                   ▼
                         Human reviews + merges PR
                                   │
                                   ▼
                         terraform apply (agent-proposed fix)
```

## Repo layout

```
terraform/
  main.tf              # root module wiring vpc/alb/ecs/rds/monitoring/budget
  variables.tf
  outputs.tf
  providers.tf
  modules/
    vpc/                # networking
    alb/                # load balancer + target group
    ecs/                # Fargate service + task def
    rds/                # Postgres instance
    monitoring/         # CloudWatch alarms + log groups
    budget/             # AWS Budget + SNS cost alarm
agent/
  agent.py              # main loop: detect -> diagnose -> propose -> PR
  tools.py              # tool definitions (query_cloudwatch, read_tf_state, etc.)
  requirements.txt
chaos/
  inject.py             # on-demand failure injection for live demos
  scenarios.py          # 3 failure scenarios (SG rule, connection pool, IAM)
```

## Setup

```bash
# 1. Provision infra
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars — fill in db_password, emails, and either leave
# existing_vpc_id unset (new VPC gets created) or point it at a VPC/subnets
# you already have to skip a second NAT Gateway/EIP bill
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

If reusing an existing VPC, make sure:
- the public subnets you pass already route to an Internet Gateway (for the ALB)
- the private subnets already have outbound internet access (NAT or otherwise) if your ECS tasks need to pull images or reach the Anthropic/GitHub APIs

```bash
# 2. Install agent deps
cd ../agent
pip install -r requirements.txt

# 3. Set required env vars
export ANTHROPIC_API_KEY=...
export GITHUB_TOKEN=...
export GITHUB_REPO=ccarrylab/infra-whisperer
export AWS_REGION=us-east-1

# 4. Run a live demo
python ../chaos/inject.py --scenario security_group
python agent.py --watch
# agent detects the incident, diagnoses it, and opens a PR with the fix

# 5. Tear down when done (protects your AWS credit)
cd ../terraform
terraform destroy
```

## Cost control

`modules/budget` creates an AWS Budget with a hard dollar cap and an SNS alarm at 80%/100%
of that cap. Treat this as non-optional — set `budget_limit_usd` in `terraform.tfvars` before
running `apply`, and always run `terraform destroy` after a demo session.

## What I'd do differently at scale

- Right now the "safety gate" is a GitHub PR and a human merge — good enough for a demo, not
  enough for a real client environment. In production this needs state locking, a real
  approval workflow (not just one reviewer clicking merge), and an agent identity with
  scoped-down IAM permissions instead of broad read access.
- Confidence ranking is currently just Claude reasoning over the evidence I hand it in the
  prompt — it's not backed by a rubric. A more rigorous version would score confidence based
  on how many independent signals (alarm, logs, state diff) agree, not just model judgment.
- The chaos scripts mutate infra directly via boto3, which drifts from Terraform on purpose
  to simulate a real incident — but that means state has to be refreshed before the next
  `apply`, which is easy to forget mid-demo.
- **Found via live testing, not assumption:** `UnHealthyHostCount` goes to
  `INSUFFICIENT_DATA` - not `ALARM`, and not even `OK` - once a target group fully drains to
  zero targets. I confirmed this by scaling ECS to 0 and watching both the raw CloudWatch
  metric (no datapoints at all once targets hit `draining` state) and the alarm own state
  reason. Added a second alarm on `HealthyHostCount < 1` with `treat_missing_data =
  "breaching"`, and verified it correctly fires `ALARM` in the same test scenario where the
  original alarm sits at `INSUFFICIENT_DATA`. This is exactly the kind of total-outage gap a
  real incident-response system cannot afford to miss.
- **A real blind spot, found by testing the iam_role chaos scenario:** when I detached the
  ECS execution role policy and forced a deployment, new tasks failed with an IAM
  AccessDeniedException on logs:CreateLogStream - a genuine live incident. But it never
  showed up in any CloudWatch alarm (metrics stayed healthy the whole time, since the old
  tasks kept serving traffic under the rolling deployment minimumHealthyPercent setting),
  and the agent log-reading tool had nothing to find either, since the failure is precisely
  what prevented new logs from being written. The agent re-diagnosed the same old
  max_connections risk instead of catching the actual incident, and opened a duplicate PR.
  The real fix is not in this codebase yet: the agent needs a tool that reads ECS service
  events directly, since that is the only place this class of failure is visible.
- **A more subtle failure mode, found right after fixing the one above:** once I added the
  ECS-events tool and re-ran the agent, it did check the new tool - but then wove those real
  events into an incorrect story. It saw task churn from an unrelated forced redeployment
  (me testing the IAM fix) and, because max_connections=20 already existed in Terraform
  state, built a plausible-sounding narrative connecting the two: "the deploy caused a
  connection spike that exceeded max_connections." There was no actual evidence for this -
  no DB error logs, no connections alarm activity - just two things that happened to be
  temporally close. More tooling did not just close a blind spot, it also gave the agent
  more raw material to build a confident-sounding but factually wrong causal story from.
  This is exactly why the human-approval gate matters: an agent that sounds sure of itself
  is not the same as an agent that is right, and a PR still has to be checked against real
  evidence before it is merged, not just trusted because the write-up reads well.

