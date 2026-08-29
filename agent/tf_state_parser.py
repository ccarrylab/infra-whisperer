"""
tf_state_parser.py

Improved Terraform state parser that understands resource relationships.

The original blind spot: a security group rule was refactored from inline 
(aws_security_group.ingress) to a separate resource (aws_security_group_rule).
The flat key-value dump made the agent think the rule had disappeared.

This parser:
1. Builds a resource graph (what references what)
2. Resolves "effective presence" — is the capability still there, just in a different form?
3. Provides semantic queries the agent can use

Drop-in: place in agent/ alongside tools.py
"""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Any


@dataclass
class TerraformResource:
    """A parsed Terraform resource with its relationships."""
    address: str
    type: str
    name: str
    mode: str
    provider: str
    attributes: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)
    referenced_by: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)

    def get_attribute(self, path: str, default=None):
        parts = path.split(".")
        val = self.attributes
        for part in parts:
            if isinstance(val, dict) and part in val:
                val = val[part]
            elif isinstance(val, list) and part.isdigit():
                idx = int(part)
                if idx < len(val):
                    val = val[idx]
                else:
                    return default
            else:
                return default
        return val


@dataclass
class SecurityGroupAnalysis:
    """Semantic analysis of a security group and its rules."""
    sg_address: str
    sg_id: str
    inline_ingress_rules: List[Dict] = field(default_factory=list)
    inline_egress_rules: List[Dict] = field(default_factory=list)
    external_ingress_rules: List[Dict] = field(default_factory=list)
    external_egress_rules: List[Dict] = field(default_factory=list)
    effective_ingress_rules: List[Dict] = field(default_factory=list)
    effective_egress_rules: List[Dict] = field(default_factory=list)

    def has_ingress_rule(self, protocol: str, from_port: int, to_port: int, 
                         cidr_blocks: Optional[List[str]] = None) -> bool:
        for rule in self.effective_ingress_rules:
            if (rule.get("protocol") == protocol and 
                rule.get("from_port") == from_port and 
                rule.get("to_port") == to_port):
                if cidr_blocks is None:
                    return True
                rule_cidrs = set(rule.get("cidr_blocks", []))
                if rule_cidrs == set(cidr_blocks):
                    return True
        return False

    def get_rule_source(self, protocol: str, from_port: int, to_port: int) -> str:
        for rule in self.inline_ingress_rules:
            if (rule.get("protocol") == protocol and 
                rule.get("from_port") == from_port and 
                rule.get("to_port") == to_port):
                return f"inline in {self.sg_address}"
        for rule in self.external_ingress_rules:
            if (rule.get("protocol") == protocol and 
                rule.get("from_port") == from_port and 
                rule.get("to_port") == to_port):
                return f"external resource: {rule.get('_source_address', 'unknown')}"
        return "not found"


class TerraformStateParser:
    def __init__(self, state: Dict):
        self.raw_state = state
        self.resources: Dict[str, TerraformResource] = {}
        self._parse_resources()
        self._build_relationship_graph()

    @classmethod
    def from_file(cls, path: str) -> "TerraformStateParser":
        with open(path, "r") as f:
            state = json.load(f)
        return cls(state)

    @classmethod
    def from_json(cls, state_json: str) -> "TerraformStateParser":
        return cls(json.loads(state_json))

    def _parse_resources(self):
        for module in self.raw_state.get("resources", []):
            for instance in module.get("instances", []):
                addr = module.get("address", "")
                if not addr:
                    # Construct address from type and name
                    res_type = module.get("type", "")
                    res_name = module.get("name", "")
                    if res_type and res_name:
                        addr = f"{res_type}.{res_name}"
                    else:
                        continue
                res = TerraformResource(
                    address=addr,
                    type=module.get("type", ""),
                    name=module.get("name", ""),
                    mode=module.get("mode", "managed"),
                    provider=module.get("provider", ""),
                    attributes=instance.get("attributes", {}),
                    dependencies=instance.get("dependencies", []),
                )
                self.resources[addr] = res

    def _build_relationship_graph(self):
        for addr, res in self.resources.items():
            refs = self._extract_references(res.attributes)
            res.references = list(set(refs))
            for dep in res.dependencies:
                if dep in self.resources and dep not in res.references:
                    res.references.append(dep)

        for addr, res in self.resources.items():
            for ref in res.references:
                if ref in self.resources:
                    self.resources[ref].referenced_by.append(addr)

    def _extract_references(self, obj: Any, found: Optional[Set[str]] = None) -> List[str]:
        if found is None:
            found = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str):
                    for addr in self.resources:
                        if v == addr or addr.endswith(f".{v}") or v in addr:
                            if v == addr or (len(v) > 3 and addr.replace("module.", "").endswith(v)):
                                found.add(addr)
                else:
                    self._extract_references(v, found)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_references(item, found)
        return list(found)

    def get_resource(self, address: str) -> Optional[TerraformResource]:
        if address in self.resources:
            return self.resources[address]
        matches = [r for r in self.resources if address in r]
        if len(matches) == 1:
            return self.resources[matches[0]]
        return None

    def find_resources_by_type(self, resource_type: str) -> List[TerraformResource]:
        return [r for r in self.resources.values() if r.type == resource_type]

    def analyze_security_group(self, sg_address: str) -> Optional[SecurityGroupAnalysis]:
        sg = self.get_resource(sg_address)
        if not sg or sg.type != "aws_security_group":
            return None

        analysis = SecurityGroupAnalysis(
            sg_address=sg_address,
            sg_id=sg.get_attribute("id", ""),
        )

        analysis.inline_ingress_rules = sg.get_attribute("ingress", []) or []
        analysis.inline_egress_rules = sg.get_attribute("egress", []) or []

        external_rules = self.find_resources_by_type("aws_security_group_rule")
        for rule in external_rules:
            sg_id_attr = rule.get_attribute("security_group_id", "")
            if sg_id_attr == analysis.sg_id or sg_id_attr == sg_address:
                rule_dict = {
                    "_source_address": rule.address,
                    "type": rule.get_attribute("type", "ingress"),
                    "protocol": rule.get_attribute("protocol", ""),
                    "from_port": rule.get_attribute("from_port", 0),
                    "to_port": rule.get_attribute("to_port", 0),
                    "cidr_blocks": rule.get_attribute("cidr_blocks", []),
                    "source_security_group_id": rule.get_attribute("source_security_group_id", None),
                }
                if rule_dict["type"] == "ingress":
                    analysis.external_ingress_rules.append(rule_dict)
                else:
                    analysis.external_egress_rules.append(rule_dict)

        seen = set()
        for rule in analysis.inline_ingress_rules + analysis.external_ingress_rules:
            key = (rule.get("protocol"), rule.get("from_port"), rule.get("to_port"), 
                   tuple(sorted(rule.get("cidr_blocks", []))))
            if key not in seen:
                seen.add(key)
                analysis.effective_ingress_rules.append(rule)

        seen = set()
        for rule in analysis.inline_egress_rules + analysis.external_egress_rules:
            key = (rule.get("protocol"), rule.get("from_port"), rule.get("to_port"),
                   tuple(sorted(rule.get("cidr_blocks", []))))
            if key not in seen:
                seen.add(key)
                analysis.effective_egress_rules.append(rule)

        return analysis

    def find_drift(self, expected_rules: List[Dict]) -> List[Dict]:
        sgs = self.find_resources_by_type("aws_security_group")
        drift = []

        for expected in expected_rules:
            sg_addr = expected.get("security_group_address")
            sg = self.analyze_security_group(sg_addr)
            if not sg:
                drift.append({
                    "type": "security_group_missing",
                    "expected_sg": sg_addr,
                    "severity": "critical"
                })
                continue

            proto = expected.get("protocol", "tcp")
            from_port = expected.get("from_port")
            to_port = expected.get("to_port")
            cidrs = expected.get("cidr_blocks")

            if not sg.has_ingress_rule(proto, from_port, to_port, cidrs):
                drift.append({
                    "type": "missing_ingress_rule",
                    "security_group": sg_addr,
                    "security_group_id": sg.sg_id,
                    "protocol": proto,
                    "from_port": from_port,
                    "to_port": to_port,
                    "expected_cidrs": cidrs,
                    "actual_rules_count": len(sg.effective_ingress_rules),
                    "severity": "high",
                    "note": "Rule is genuinely missing, not just refactored to another resource"
                })

        return drift

    def generate_agent_report(self, sg_address: str) -> str:
        sg = self.analyze_security_group(sg_address)
        if not sg:
            return f"Security group {sg_address} not found in Terraform state."

        lines = [
            f"Security Group Analysis: {sg_address}",
            f"  ID: {sg.sg_id}",
            f"  Total effective ingress rules: {len(sg.effective_ingress_rules)}",
            f"    - Inline: {len(sg.inline_ingress_rules)}",
            f"    - External (aws_security_group_rule): {len(sg.external_ingress_rules)}",
            f"  Total effective egress rules: {len(sg.effective_egress_rules)}",
            f"    - Inline: {len(sg.inline_egress_rules)}",
            f"    - External (aws_security_group_rule): {len(sg.external_egress_rules)}",
            "",
            "  Effective ingress rules:",
        ]

        for rule in sg.effective_ingress_rules:
            source = "inline" if rule in sg.inline_ingress_rules else f"external: {rule.get('_source_address', 'unknown')}"
            cidrs = ", ".join(rule.get("cidr_blocks", ["N/A"]))
            lines.append(f"    - {rule.get('protocol', 'tcp')}/{rule.get('from_port', 0)}-{rule.get('to_port', 0)} from {cidrs} [{source}]")

        return "\n".join(lines)


def tool_read_terraform_state_enhanced(state_path: str, query_type: str = "security_group", 
                                       target: str = "") -> str:
    try:
        parser = TerraformStateParser.from_file(state_path)
    except Exception as e:
        return f"Error parsing Terraform state: {e}"

    if query_type == "security_group":
        return parser.generate_agent_report(target)

    elif query_type == "drift_check":
        expected = [
            {
                "security_group_address": target,
                "protocol": "tcp",
                "from_port": 80,
                "to_port": 80,
                "cidr_blocks": ["0.0.0.0/0"]
            }
        ]
        drift = parser.find_drift(expected)
        if not drift:
            return f"No genuine drift detected for {target}. All expected capabilities are present."

        lines = [f"GENUINE DRIFT DETECTED for {target}:"]
        for d in drift:
            lines.append(f"  - {d['type']}: {d.get('protocol', '')}/{d.get('from_port', '')} missing")
            lines.append(f"    Severity: {d['severity']}")
            lines.append(f"    Note: {d['note']}")
        return "\n".join(lines)

    elif query_type == "resource_graph":
        res = parser.get_resource(target)
        if not res:
            return f"Resource {target} not found."

        lines = [
            f"Resource: {res.address}",
            f"  Type: {res.type}",
            f"  Referenced by: {res.referenced_by or 'nothing'}",
            f"  References: {res.references or 'nothing'}",
        ]
        return "\n".join(lines)

    return f"Unknown query_type: {query_type}"


def demo():
    mock_state = {
        "version": 4,
        "terraform_version": "1.5.0",
        "resources": [
            {
                "mode": "managed",
                "type": "aws_security_group",
                "name": "ecs_service",
                "provider": "provider[registry.terraform.io/hashicorp/aws]",
                "instances": [
                    {
                        "attributes": {
                            "id": "sg-12345678",
                            "name": "ecs-service-sg",
                            "ingress": [],
                            "egress": [
                                {
                                    "protocol": "-1",
                                    "from_port": 0,
                                    "to_port": 0,
                                    "cidr_blocks": ["0.0.0.0/0"]
                                }
                            ]
                        },
                        "dependencies": []
                    }
                ]
            },
            {
                "mode": "managed",
                "type": "aws_security_group_rule",
                "name": "alb_ingress",
                "provider": "provider[registry.terraform.io/hashicorp/aws]",
                "instances": [
                    {
                        "attributes": {
                            "id": "sgrule-123456",
                            "security_group_id": "sg-12345678",
                            "type": "ingress",
                            "protocol": "tcp",
                            "from_port": 80,
                            "to_port": 80,
                            "cidr_blocks": ["0.0.0.0/0"],
                            "source_security_group_id": None
                        },
                        "dependencies": ["aws_security_group.ecs_service"]
                    }
                ]
            }
        ]
    }

    print("=" * 70)
    print("TERRAFORM STATE PARSER DEMONSTRATION")
    print("=" * 70)
    print("\nScenario: Security group rule refactored from inline to external resource")
    print("(This is exactly the blind spot from the README)\n")

    parser = TerraformStateParser(mock_state)

    sg = parser.get_resource("aws_security_group.ecs_service")
    inline_rules = sg.get_attribute("ingress", []) if sg else []
    print(f"Naive check (inline only): {len(inline_rules)} ingress rules")
    print(f"  -> Agent thinks: 'RULE IS MISSING!'")

    analysis = parser.analyze_security_group("aws_security_group.ecs_service")
    print(f"\nEnhanced check (effective rules): {len(analysis.effective_ingress_rules)} ingress rules")
    print(f"  Inline: {len(analysis.inline_ingress_rules)}")
    print(f"  External: {len(analysis.external_ingress_rules)}")

    has_rule = analysis.has_ingress_rule("tcp", 80, 80, ["0.0.0.0/0"])
    print(f"\nHas HTTP ingress rule? {has_rule}")
    print(f"  -> Agent learns: 'Rule exists in {analysis.get_rule_source('tcp', 80, 80)}'")

    print("\n" + "-" * 70)
    print("DRIFT CHECK")
    print("-" * 70)
    expected = [
        {
            "security_group_address": "aws_security_group.ecs_service",
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": ["0.0.0.0/0"]
        },
        {
            "security_group_address": "aws_security_group.ecs_service",
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            "cidr_blocks": ["0.0.0.0/0"]
        }
    ]
    drift = parser.find_drift(expected)
    for d in drift:
        print(f"  Drift: {d['type']} — {d['note']}")

    print("\n" + "-" * 70)
    print("AGENT REPORT")
    print("-" * 70)
    print(parser.generate_agent_report("aws_security_group.ecs_service"))

    print("\n" + "=" * 70)
    print("SIMULATION: Rule genuinely revoked (real incident)")
    print("=" * 70)

    mock_state_broken = {
        "version": 4,
        "terraform_version": "1.5.0",
        "resources": [
            {
                "mode": "managed",
                "type": "aws_security_group",
                "name": "ecs_service",
                "provider": "provider[registry.terraform.io/hashicorp/aws]",
                "instances": [
                    {
                        "attributes": {
                            "id": "sg-12345678",
                            "name": "ecs-service-sg",
                            "ingress": [],
                            "egress": [
                                {
                                    "protocol": "-1",
                                    "from_port": 0,
                                    "to_port": 0,
                                    "cidr_blocks": ["0.0.0.0/0"]
                                }
                            ]
                        },
                        "dependencies": []
                    }
                ]
            }
        ]
    }

    parser_broken = TerraformStateParser(mock_state_broken)
    analysis_broken = parser_broken.analyze_security_group("aws_security_group.ecs_service")
    has_rule_broken = analysis_broken.has_ingress_rule("tcp", 80, 80, ["0.0.0.0/0"])
    print(f"Has HTTP ingress rule after chaos? {has_rule_broken}")
    print(f"  -> This is a GENUINE finding. The agent should report this as real drift.")

    drift_broken = parser_broken.find_drift(expected[:1])
    for d in drift_broken:
        print(f"  CONFIRMED DRIFT: {d['type']} on {d['security_group']}")


if __name__ == "__main__":
    demo()
