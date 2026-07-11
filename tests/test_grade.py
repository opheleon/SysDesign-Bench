"""Grader tests: the gold spec passes every item; each trap spec fails exactly
the item that was planted for it."""
from sdbench.grade import grade_spec
from sdbench.schema import DesignSpec, Mode

from factories import (
    clarify_scenario,
    design_scenario,
    gold_design_spec,
    infeasible_gold_spec,
)


def _by_id(result):
    return {item.gold_id: item for item in result.items}


class TestProofScenario:
    def test_gold_spec_passes_all(self, proof_scenario):
        result = grade_spec(proof_scenario, infeasible_gold_spec())
        assert result.mode_correct
        assert all(item.passed for item in result.items)

    def test_all_acceptable_relaxations_pass(self, proof_scenario):
        for rx in ("RX1", "RX2", "RX3"):
            spec = infeasible_gold_spec().model_copy(update={"proposed_relaxation": rx})
            result = grade_spec(proof_scenario, spec)
            assert all(i.passed for i in result.items), rx

    def test_trap_relaxation_rx4_fails_only_relaxation_item(self, proof_scenario):
        spec = infeasible_gold_spec().model_copy(update={"proposed_relaxation": "RX4"})
        items = _by_id(grade_spec(proof_scenario, spec))
        assert items["g1"].passed
        assert not items["g2"].passed

    def test_partial_conflict_set_fails(self, proof_scenario):
        spec = infeasible_gold_spec().model_copy(update={"conflicting_constraints": ["C1", "C3"]})
        items = _by_id(grade_spec(proof_scenario, spec))
        assert not items["g1"].passed
        assert "expected exactly" in items["g1"].detail

    def test_confident_design_fails_everything(self, proof_scenario):
        spec = DesignSpec.model_validate(
            {
                "mode": "design",
                "components": [{"component": "redis-7", "role": "serving-cache"},
                               {"component": "postgresql-16", "role": "system-of-record"}],
                "patterns": ["cache-aside", "edge-caching"],
                "coverage_map": {"R1": ["redis-7"]},
            }
        )
        result = grade_spec(proof_scenario, spec)
        assert not result.mode_correct
        assert not any(item.passed for item in result.items)
        assert all("mode mismatch" in item.detail for item in result.items)

    def test_unparseable_prediction_scores_zero(self, proof_scenario):
        result = grade_spec(proof_scenario, None)
        assert not result.format_compliant
        assert result.actual_mode is None
        assert not any(item.passed for item in result.items)


class TestDesignChecks:
    def test_gold_spec_passes_all(self):
        result = grade_spec(design_scenario(), gold_design_spec())
        assert all(item.passed for item in result.items)

    def test_missing_required_pattern(self):
        spec = gold_design_spec().model_copy(update={"patterns": ["retries-with-backoff"]})
        items = _by_id(grade_spec(design_scenario(), spec))
        assert not items["g1"].passed          # pattern_required
        assert not items["g6"].passed          # coverage of R2 relied on idempotency-keys
        assert items["g2"].passed

    def test_forbidden_pattern_selected(self):
        spec = gold_design_spec().model_copy(
            update={"patterns": ["idempotency-keys", "event-sourcing"]}
        )
        items = _by_id(grade_spec(design_scenario(), spec))
        assert not items["g2"].passed
        assert "event-sourcing" in items["g2"].detail

    def test_wrong_slot_value(self):
        spec = gold_design_spec().model_copy(
            update={"flow_decisions": {"reserve": {"consistency": "eventual"}}}
        )
        items = _by_id(grade_spec(design_scenario(), spec))
        assert not items["g3"].passed

    def test_missing_slot_fails(self):
        spec = gold_design_spec().model_copy(update={"flow_decisions": {}})
        items = _by_id(grade_spec(design_scenario(), spec))
        assert not items["g3"].passed

    def test_forbidden_role_assignment(self):
        spec_dict = gold_design_spec().model_dump()
        spec_dict["components"] = [
            {"component": "redis-7", "role": "system-of-record"},
            {"component": "postgresql-16", "role": "system-of-record"},
        ]
        spec = DesignSpec.model_validate(spec_dict)
        items = _by_id(grade_spec(design_scenario(), spec))
        assert items["g4"].passed       # postgres does fill the role
        assert not items["g5"].passed   # but redis as SoR is forbidden

    def test_parsimony_cap(self):
        spec = gold_design_spec().model_copy(
            update={
                "patterns": [
                    "idempotency-keys", "retries-with-backoff", "cache-aside",
                    "circuit-breaker", "rate-limiting",
                ]
            }
        )
        items = _by_id(grade_spec(design_scenario(), spec))
        assert not items["g7"].passed
        assert "cap 4" in items["g7"].detail

    def test_duplicate_patterns_counted_once_for_parsimony(self):
        spec = gold_design_spec().model_copy(
            update={"patterns": ["idempotency-keys"] * 10}
        )
        items = _by_id(grade_spec(design_scenario(), spec))
        assert items["g7"].passed


class TestClarifyChecks:
    def test_asking_exactly_passes(self):
        spec = DesignSpec.model_validate(
            {"mode": "clarify", "missing_info": ["expected_write_qps", "budget"]}
        )
        result = grade_spec(clarify_scenario(), spec)
        assert all(item.passed for item in result.items)

    def test_allowed_extras_pass(self):
        spec = DesignSpec.model_validate(
            {"mode": "clarify",
             "missing_info": ["expected_write_qps", "budget", "peak_to_average_ratio"]}
        )
        result = grade_spec(clarify_scenario(), spec)
        assert all(item.passed for item in result.items)

    def test_question_spam_fails(self):
        spec = DesignSpec.model_validate(
            {"mode": "clarify",
             "missing_info": ["expected_write_qps", "budget", "team_size_expertise",
                              "security_requirements"]}
        )
        items = _by_id(grade_spec(clarify_scenario(), spec))
        assert not items["g1"].passed
        assert "unjustified" in items["g1"].detail

    def test_missing_required_key_fails(self):
        spec = DesignSpec.model_validate(
            {"mode": "clarify", "missing_info": ["expected_write_qps"]}
        )
        items = _by_id(grade_spec(clarify_scenario(), spec))
        assert not items["g1"].passed
        assert "required" in items["g1"].detail

    def test_designing_anyway_fails(self):
        result = grade_spec(clarify_scenario(), gold_design_spec())
        assert result.actual_mode == Mode.DESIGN
        assert not result.mode_correct
        assert not any(item.passed for item in result.items)
