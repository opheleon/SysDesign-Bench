import pytest
from pydantic import ValidationError

from sdbench.schema import (
    Catalogs,
    DesignSpec,
    Mode,
    Scenario,
)


class TestCatalogs:
    def test_catalogs_load(self, catalogs: Catalogs):
        assert catalogs.version == "0.3.0"
        assert len(catalogs.patterns) >= 80, "pattern catalog should be large enough that it leaks no hints"
        assert len(catalogs.components) >= 10
        assert len(catalogs.roles) >= 8
        assert len(catalogs.missing_info) >= 15

    def test_catalog_id_sets(self, catalogs: Catalogs):
        assert "cache-aside" in catalogs.pattern_ids
        assert "postgresql-16" in catalogs.component_ids
        assert "system-of-record" in catalogs.role_ids
        assert "expected_write_qps" in catalogs.missing_info_ids
        assert catalogs.choice_ids == catalogs.pattern_ids | catalogs.component_ids

    def test_pilot_scenario_pattern_dependencies_exist(self, catalogs: Catalogs):
        """Patterns the approved pilot scenarios (M3) will reference must exist now."""
        needed = {
            "gap-detection", "staleness-fallback", "ab-feed-arbitration", "kill-switch",
            "udp-custom-reliability", "deterministic-lockstep", "input-log-replay",
            "client-prediction", "lag-compensation", "server-authoritative-state",
            "interest-management", "backpressure", "fail-closed",
            "temporal-audit-log", "read-replicas",
            "scheduled-pre-provisioning", "queue-load-leveling", "virtual-waiting-room",
            "load-shedding", "autoscaling",
            "expand-contract", "dual-write", "cdc-replication", "backfill",
            "rollback-plan", "data-verification-reconciliation",
            "idempotency-keys",
        }
        missing = needed - catalogs.pattern_ids
        assert not missing, f"pattern catalog missing: {sorted(missing)}"

    def test_v03_catalog_additions_exist(self, catalogs: Catalogs):
        patterns = {
            "fencing-tokens", "version-guarded-writes",
            "failure-domain-aware-placement", "consistent-cut-snapshot",
            "bitemporal-model", "tail-based-sampling", "point-in-time-join",
            "gang-scheduling",
        }
        missing_info = {
            "server_trust_model", "membership_revocation_scope",
            "privacy_unit", "privacy_budget_definition",
        }
        assert patterns <= catalogs.pattern_ids
        assert missing_info <= catalogs.missing_info_ids


class TestHaystackPairs:
    """Short/long haystack siblings must keep IDENTICAL gold sets and flows,
    or the pair's score delta stops isolating extraction-under-load."""

    PAIRS = [
        ("omnichannel-inventory-001", "omnichannel-inventory-haystack-001"),
    ]

    def test_pair_gold_and_flow_identity(self):
        import yaml
        from conftest import SCENARIO_DIR

        for short_name, long_name in self.PAIRS:
            short = yaml.safe_load(open(SCENARIO_DIR / f"{short_name}.yaml"))
            long_ = yaml.safe_load(open(SCENARIO_DIR / f"{long_name}.yaml"))
            assert short["gold"] == long_["gold"], f"gold sets differ: {short_name} vs {long_name}"
            assert short["flows"] == long_["flows"], f"flows differ: {short_name} vs {long_name}"
            assert short["expected_mode"] == long_["expected_mode"]


class TestScenario:
    def test_proof_scenario_parses(self, proof_scenario: Scenario):
        assert proof_scenario.instance_id == "retail-inventory-lookup-001"
        assert proof_scenario.expected_mode == Mode.INFEASIBLE
        assert len(proof_scenario.constraints) == 4
        assert {g.id for g in proof_scenario.gold} == {"g1", "g2"}

    def test_unknown_field_rejected(self, proof_scenario_dict: dict):
        proof_scenario_dict["surprise_field"] = "nope"
        with pytest.raises(ValidationError):
            Scenario.model_validate(proof_scenario_dict)

    def test_unknown_gold_check_type_rejected(self, proof_scenario_dict: dict):
        proof_scenario_dict["gold"].append(
            {"id": "g99", "dimension": "operability", "check": "vibes", "acceptable": ["good"]}
        )
        with pytest.raises(ValidationError):
            Scenario.model_validate(proof_scenario_dict)


class TestDesignSpec:
    def test_infeasible_spec(self):
        spec = DesignSpec.model_validate(
            {
                "mode": "infeasible",
                "conflicting_constraints": ["C1", "C2", "C3", "C4"],
                "proposed_relaxation": "RX2",
                "notes": "WAN RTT floor exceeds the latency budget.",
            }
        )
        assert spec.mode == Mode.INFEASIBLE
        assert spec.patterns == []

    def test_design_spec(self):
        spec = DesignSpec.model_validate(
            {
                "mode": "design",
                "components": [{"component": "postgresql-16", "role": "system-of-record"}],
                "patterns": ["temporal-audit-log"],
                "flow_decisions": {"reads": {"consistency": "strong"}},
                "coverage_map": {"R1": ["postgresql-16"]},
            }
        )
        assert spec.components[0].role == "system-of-record"

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            DesignSpec.model_validate({"mode": "design", "free_text_design": "essay"})
