"""Validator tests: the proof scenario lints clean, and each authoring-bar rule
actually rejects a scenario that breaks it."""
import pytest

from sdbench.schema import Catalogs, Scenario
from sdbench.validate import validate_catalogs, validate_scenario


def _revalidate(scenario_dict: dict, catalogs: Catalogs) -> list[str]:
    return validate_scenario(Scenario.model_validate(scenario_dict), catalogs)


def test_catalogs_valid(catalogs: Catalogs):
    assert validate_catalogs(catalogs) == []


def test_proof_scenario_valid(proof_scenario, catalogs: Catalogs):
    assert validate_scenario(proof_scenario, catalogs) == []


class TestValidatorRejects:
    def test_unknown_constraint_in_conflict_set(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["gold"][0]["expected"] = ["C1", "C9"]
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("unknown constraint 'C9'" in e for e in errors)

    def test_single_conflicting_constraint_rejected(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["gold"][0]["expected"] = ["C1"]
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any(">= 2 conflicting constraint ids" in e for e in errors)

    def test_unknown_relaxation_option(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["gold"][1]["acceptable"] = ["RX1", "RX9"]
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("unknown relaxation option 'RX9'" in e for e in errors)

    def test_trap_with_unknown_constraint(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["traps"][0]["violated_constraint_id"] = "C42"
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("unknown violated_constraint_id 'C42'" in e for e in errors)

    def test_no_traps_rejected(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["traps"] = []
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("at least one trap is required" in e for e in errors)

    def test_twist_without_gold_refs_rejected(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["twist"]["twist_gold_refs"] = []
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("twist must reference at least one gold item" in e for e in errors)

    def test_twist_with_unknown_gold_ref(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["twist"]["twist_gold_refs"] = ["g77"]
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("unknown gold item 'g77'" in e for e in errors)

    def test_stub_gold_rationale_rejected(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["gold_rationale"] = "trust me"
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("gold_rationale too short" in e for e in errors)

    def test_infeasible_without_relaxation_check(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["gold"] = [proof_scenario_dict["gold"][0]]
        proof_scenario_dict["twist"]["twist_gold_refs"] = ["g1"]
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("missing a relaxation gold check" in e for e in errors)

    def test_duplicate_gold_ids_rejected(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["gold"][1]["id"] = "g1"
        proof_scenario_dict["twist"]["twist_gold_refs"] = ["g1"]
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("duplicate gold item id 'g1'" in e for e in errors)

    def test_unknown_pattern_in_gold(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["gold"].append(
            {
                "id": "g3",
                "dimension": "operability",
                "check": "pattern_required",
                "acceptable": ["blockchain-all-the-things"],
            }
        )
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("unknown pattern 'blockchain-all-the-things'" in e for e in errors)

    def test_design_mode_without_design_checks(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["expected_mode"] = "design"
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("no matching check" in e for e in errors)

    def test_missing_info_key_outside_taxonomy(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["expected_mode"] = "clarify"
        proof_scenario_dict["gold"].append(
            {
                "id": "g3",
                "dimension": "clarification_seeking",
                "check": "missing_info",
                "required": ["favorite_color"],
                "allowed": ["favorite_color"],
            }
        )
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("unknown missing_info key 'favorite_color'" in e for e in errors)

    def test_slot_acceptable_outside_allowed_options(self, proof_scenario_dict, catalogs):
        proof_scenario_dict["flows"] = [
            {
                "id": "reads",
                "description": "availability reads",
                "decision_slots": {"consistency": ["strong", "eventual"]},
            }
        ]
        proof_scenario_dict["gold"].append(
            {
                "id": "g3",
                "dimension": "tradeoff_selection",
                "check": "slot_match",
                "flow": "reads",
                "slot": "consistency",
                "acceptable": ["causal"],
            }
        )
        errors = _revalidate(proof_scenario_dict, catalogs)
        assert any("not among the slot's allowed options" in e for e in errors)
