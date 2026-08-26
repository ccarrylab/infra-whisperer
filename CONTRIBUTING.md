# Contributing

This started as a personal portfolio project, so I'm not actively looking for
outside contributions - but if something here is useful to you and you want to
extend it, here's what you'd need to know.

## Running the test suite

```bash
pip install -r agent/requirements.txt
pytest tests/ -v
```

Tests are fully mocked - no AWS credentials or live infrastructure required.
CI runs this automatically on every push and PR (see `.github/workflows/tests.yml`).

## Running it live

That's a different story - see the main [README](README.md) setup section.
Live testing requires your own AWS account, an Anthropic API key, and a
GitHub token, and it will provision real infrastructure. `demo.sh` is the
fastest path to a full end-to-end run.

## Known gaps if you want to extend this

- The `connection_pool` chaos scenario was never run live during development
  (it needs a temporary public RDS endpoint, a tradeoff I didn't take - see
  the README's cost/security notes). `chaos/scenarios.py::exhaust_connection_pool`
  is also not unit tested for the same reason it's hard to run live: it blocks
  on an interactive loop waiting for a KeyboardInterrupt. A real fix would
  refactor it to accept an injectable stop condition.
- The agent has no visibility into RDS-level metrics beyond what
  `query_cloudwatch` already covers - a `query_rds_performance_insights` tool
  would be a natural next addition.
- Confidence ranking is currently pure LLM judgment, not backed by a scoring
  rubric. See the README's "what I'd do differently at scale" section for the
  fuller reasoning on this.

## Pull requests

If you do send one: keep it scoped, include a test if you're touching
`agent/tools.py` or `chaos/scenarios.py`, and describe what you actually ran
to validate it - that's the standard this whole repo tries to hold itself to.
