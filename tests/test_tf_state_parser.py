"""
Tests for tf_state_parser.py.

Validates the semantic Terraform state parser that fixes the blind spot
where security group rules refactored from inline to external resources
appeared missing in raw state.

Run with:
    pytest tests/test_tf_state_parser.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from tf_state_parser import TerraformStateParser, SecurityGroupAnalysis


class TestTerraformStateParser:
    def test_parses_basic_state(self):
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "test",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-test",
                            "ingress": [{"protocol": "tcp", "from_port": 80, "to_port": 80, "cidr_blocks": ["0.0.0.0/0"]}],
                            "egress": []
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        sg = parser.analyze_security_group("aws_security_group.test")
        assert sg is not None
        assert sg.sg_id == "sg-test"
        assert len(sg.inline_ingress_rules) == 1

    def test_finds_external_rule(self):
        """The core blind-spot fix."""
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-123",
                            "ingress": [],
                            "egress": []
                        },
                        "dependencies": []
                    }]
                },
                {
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "name": "http",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "security_group_id": "sg-123",
                            "type": "ingress",
                            "protocol": "tcp",
                            "from_port": 80,
                            "to_port": 80,
                            "cidr_blocks": ["0.0.0.0/0"],
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        sg = parser.analyze_security_group("aws_security_group.ecs")
        assert sg.has_ingress_rule("tcp", 80, 80, ["0.0.0.0/0"]) is True
        assert len(sg.effective_ingress_rules) == 1
        assert len(sg.inline_ingress_rules) == 0
        assert len(sg.external_ingress_rules) == 1

    def test_deduplicates_equivalent_rules(self):
        """If same rule exists both inline and external, count once."""
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-123",
                            "ingress": [{"protocol": "tcp", "from_port": 80, "to_port": 80, "cidr_blocks": ["0.0.0.0/0"]}],
                            "egress": []
                        },
                        "dependencies": []
                    }]
                },
                {
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "name": "http",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "security_group_id": "sg-123",
                            "type": "ingress",
                            "protocol": "tcp",
                            "from_port": 80,
                            "to_port": 80,
                            "cidr_blocks": ["0.0.0.0/0"],
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        sg = parser.analyze_security_group("aws_security_group.ecs")
        assert len(sg.effective_ingress_rules) == 1  # deduplicated

    def test_detects_genuine_missing_rule(self):
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-123",
                            "ingress": [],
                            "egress": []
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        sg = parser.analyze_security_group("aws_security_group.ecs")
        assert sg.has_ingress_rule("tcp", 80, 80, ["0.0.0.0/0"]) is False

    def test_get_rule_source_inline(self):
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-123",
                            "ingress": [{"protocol": "tcp", "from_port": 443, "to_port": 443, "cidr_blocks": ["0.0.0.0/0"]}],
                            "egress": []
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        sg = parser.analyze_security_group("aws_security_group.ecs")
        source = sg.get_rule_source("tcp", 443, 443)
        assert "inline" in source

    def test_get_rule_source_external(self):
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-123",
                            "ingress": [],
                            "egress": []
                        },
                        "dependencies": []
                    }]
                },
                {
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "name": "https",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "security_group_id": "sg-123",
                            "type": "ingress",
                            "protocol": "tcp",
                            "from_port": 443,
                            "to_port": 443,
                            "cidr_blocks": ["0.0.0.0/0"],
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        sg = parser.analyze_security_group("aws_security_group.ecs")
        source = sg.get_rule_source("tcp", 443, 443)
        assert "external" in source
        assert "aws_security_group_rule.https" in source

    def test_find_drift_reports_only_genuine(self):
        """Drift check should not report refactored rules as missing."""
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "id": "sg-123",
                            "ingress": [],
                            "egress": []
                        },
                        "dependencies": []
                    }]
                },
                {
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "name": "http",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "security_group_id": "sg-123",
                            "type": "ingress",
                            "protocol": "tcp",
                            "from_port": 80,
                            "to_port": 80,
                            "cidr_blocks": ["0.0.0.0/0"],
                        },
                        "dependencies": []
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        expected = [{
            "security_group_address": "aws_security_group.ecs",
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": ["0.0.0.0/0"]
        }]
        drift = parser.find_drift(expected)
        assert len(drift) == 0  # Rule exists as external, not genuine drift

    def test_resource_graph_builds_references(self):
        state = {
            "version": 4,
            "terraform_version": "1.5.0",
            "resources": [
                {
                    "mode": "managed",
                    "type": "aws_security_group",
                    "name": "ecs",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {"id": "sg-123"},
                        "dependencies": []
                    }]
                },
                {
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "name": "http",
                    "provider": "provider[registry.terraform.io/hashicorp/aws]",
                    "instances": [{
                        "attributes": {
                            "security_group_id": "sg-123",
                            "type": "ingress",
                            "protocol": "tcp",
                            "from_port": 80,
                            "to_port": 80,
                            "cidr_blocks": ["0.0.0.0/0"],
                        },
                        "dependencies": ["aws_security_group.ecs"]
                    }]
                }
            ]
        }
        parser = TerraformStateParser(state)
        rule = parser.get_resource("aws_security_group_rule.http")
        assert "aws_security_group.ecs" in rule.references
        sg = parser.get_resource("aws_security_group.ecs")
        assert "aws_security_group_rule.http" in sg.referenced_by
