"""
CLI entrypoint for triggering a chaos scenario live during a demo.

Usage:
    python inject.py --scenario security_group --sg-id sg-0123456789
    python inject.py --scenario connection_pool
    python inject.py --scenario iam_role --role-name infra-whisperer-ecs-execution-role \
        --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

Get the required IDs from `terraform output` in the terraform/ directory
after `apply` — outputs.tf exposes vpc_id, cluster/service names, etc.
Security group and role names/ARNs can be pulled from `terraform show -json`
or the AWS console.
"""

import argparse

from scenarios import break_iam_role, break_security_group, exhaust_connection_pool


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=["security_group", "connection_pool", "iam_role"],
        required=True,
    )
    parser.add_argument("--sg-id")
    parser.add_argument("--db-identifier")
    parser.add_argument("--role-name")
    parser.add_argument("--policy-arn")
    args = parser.parse_args()

    if args.scenario == "security_group":
        if not args.sg_id:
            raise SystemExit("--sg-id is required for the security_group scenario")
        result = break_security_group(args.sg_id)

    elif args.scenario == "connection_pool":
        result = exhaust_connection_pool(args.db_identifier or "infra-whisperer-db")

    elif args.scenario == "iam_role":
        if not args.role_name or not args.policy_arn:
            raise SystemExit("--role-name and --policy-arn are required for the iam_role scenario")
        result = break_iam_role(args.role_name, args.policy_arn)

    print(result)


if __name__ == "__main__":
    main()
