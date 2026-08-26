"""
Failure scenarios for live demos. Each scenario mutates one piece of the
Terraform-managed infrastructure directly via boto3 (not through Terraform),
simulating "someone made a manual change that drifted from IaC" — a very
common real-world incident pattern, and a great one for an FDE demo because
part of the fix is reconciling that drift back into Terraform.
"""

import boto3

_ec2 = None
_rds = None


def _get_ec2():
    global _ec2
    if _ec2 is None:
        _ec2 = boto3.client("ec2")
    return _ec2


def _get_rds():
    global _rds
    if _rds is None:
        _rds = boto3.client("rds")
    return _rds


def break_security_group(sg_id: str) -> dict:
    """Remove the ingress rule that allows the app to reach the DB.

    Simulates a manual security group edit that drifted from Terraform —
    one of the most common real incident causes.
    """
    rules = _get_ec2().describe_security_group_rules(
        Filters=[{"Name": "group-id", "Values": [sg_id]}]
    )["SecurityGroupRules"]

    ingress_rule_ids = [r["SecurityGroupRuleId"] for r in rules if not r["IsEgress"]]

    if not ingress_rule_ids:
        return {"status": "no_ingress_rules_found"}

    _get_ec2().revoke_security_group_ingress(
        GroupId=sg_id, SecurityGroupRuleIds=ingress_rule_ids[:1]
    )
    return {"status": "ingress_rule_revoked", "sg_id": sg_id, "rule_ids": ingress_rule_ids[:1]}


def exhaust_connection_pool(db_identifier: str, num_connections: int = 18) -> dict:
    """Open (and hold) many connections to the DB to approach max_connections.

    Requires psycopg2 and the DB endpoint/credentials to be available to the
    script. Held open until the process is killed — intended to be run in a
    background terminal during a demo, then interrupted (Ctrl+C) to release.
    """
    import psycopg2
    import os

    conns = []
    dsn = os.environ["DEMO_DB_DSN"]  # e.g. postgres://user:pass@host:5432/dbname
    for _ in range(num_connections):
        conns.append(psycopg2.connect(dsn))

    print(f"Holding {len(conns)} connections open. Press Ctrl+C to release.")
    try:
        while True:
            pass
    except KeyboardInterrupt:
        for c in conns:
            c.close()
    return {"status": "connections_released", "count": len(conns)}


def break_iam_role(role_name: str, policy_arn: str) -> dict:
    """Detach a required IAM policy from the ECS execution role.

    Simulates a common "someone tightened permissions and broke prod" incident.
    """
    iam = boto3.client("iam")
    iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    return {"status": "policy_detached", "role_name": role_name, "policy_arn": policy_arn}
