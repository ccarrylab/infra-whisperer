# Commit Plan

A real build order, not a staged one. Commit each section only once you've actually run it
and it works (or once you've deliberately captured a real bug you hit) — that's what makes
the history genuine instead of a dump. Rough pacing: this maps to Days 1–14 of Phase 1 in
the transition plan, so spread these across roughly two weeks as you actually do the work.

## 1. Scaffolding (Days 1–3)

- [ ] `chore: init repo structure` — just the folders + .gitignore + empty README
- [ ] `feat: vpc module` — commit once `terraform plan` runs clean against it standalone
- [ ] `feat: alb module`
- [ ] `feat: ecs module`
- [ ] `feat: rds module`
- [ ] `feat: wire modules together in root main.tf` — commit once `terraform apply` actually
      succeeds end-to-end and you can hit the ALB DNS name in a browser. This is a real
      milestone — don't commit it until it's genuinely working.

**Expect a real bug here.** Common ones: the ECS service can't reach the internet to pull
the container image (private subnet needs NAT), or the RDS security group reference is
circular with the ECS one. When you hit one, fix it and commit the fix separately —
`fix: ecs task can't pull image, private subnet had no NAT route` is a completely normal,
true commit message once you've actually debugged it.

## 2. Cost controls (still Day 1–3, do this before you leave anything running overnight)

- [ ] `feat: budget module with hard cost cap`
- [ ] `feat: existing VPC support` — once you've tested pointing it at your own VPC/subnets
      and confirmed it skips creating a new NAT gateway

## 3. Monitoring (Day 3–4)

- [ ] `feat: cloudwatch alarms for alb/ecs/rds`
- [ ] Trigger one manually (e.g. stop the ECS service) and confirm the alarm actually fires
      and the SNS email arrives before committing — if it doesn't fire, that's a real debug
      cycle worth its own fix commit.

## 4. Agent layer (Days 4–7)

- [ ] `feat: tool definitions (query_cloudwatch, query_log_group, read_terraform_state)`
- [ ] `feat: agent loop — diagnose-now mode`
- [ ] Run it against a real alarm state (even a manually-triggered one) and see what it
      actually outputs. If the diagnosis is wrong or the tool-calling loop breaks, that's a
      real commit: `fix: agent looped on read_terraform_state, state file path was relative`
      or similar — whatever actually goes wrong for you.
- [ ] `feat: propose_tf_diff + open_github_pr tools`
- [ ] `feat: watch mode`

## 5. Chaos scenarios (Days 8–10)

- [ ] `feat: security_group chaos scenario` — run it for real, confirm the agent detects and
      correctly diagnoses it, commit
- [ ] `feat: connection_pool chaos scenario` — same
- [ ] `feat: iam_role chaos scenario` — same

Do these one at a time, end-to-end, not all three written and committed at once — you'll
likely find the confidence-ranking behaves differently across scenarios, and that's worth
noting in the README once you've seen it.

## 6. Packaging (Days 11–14)

- [ ] `docs: readme with architecture, setup, cost notes`
- [ ] Record the real terminal output from a live diagnosis run and paste an excerpt into the
      README instead of a fabricated example
- [ ] `docs: what I'd do differently at scale`
- [ ] Final cleanup pass — remove any dead code or leftover debug prints you accumulated
      along the way (there will be some — that's normal)

## Rule of thumb

If you haven't run it and watched it work, don't commit it as done. A commit history that's
genuinely tied to "I built this, tested it, hit a real problem, fixed it" will read as
authentic because it is — no staging required.
