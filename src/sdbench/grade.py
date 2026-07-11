"""Deterministic grader: pure set/enum matching. Zero LLM calls.

Every gold check maps to one small pure function. If the submitted spec's mode
differs from the scenario's expected mode, all gold items fail (mode-gating):
confidently designing an infeasible system, or clarifying a fully-specified one,
forfeits the scenario's items.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .schema import (
    ConflictingConstraintsCheck,
    CoverageCheck,
    DesignSpec,
    Dimension,
    GoldCheck,
    MissingInfoCheck,
    Mode,
    ParsimonyCheck,
    PatternForbidden,
    PatternRequired,
    RelaxationCheck,
    RoleForbidden,
    RoleRequired,
    Scenario,
    SlotMatch,
)


# The checks that ARE the answer: a scenario's mode-defining verdicts.
# Design scenarios are solved by their decision slots; infeasible ones by the
# conflict set + relaxation; clarify ones by the missing-info set.
SOLUTION_CHECKS = frozenset(
    {"slot_match", "conflicting_constraints", "relaxation", "missing_info"}
)


class ItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gold_id: str
    dimension: Dimension
    check: str = ""
    weight: float = 1.0
    passed: bool
    detail: str

    @property
    def is_solution_check(self) -> bool:
        return self.check in SOLUTION_CHECKS


class ScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str
    format_compliant: bool
    expected_mode: Mode
    actual_mode: Mode | None
    mode_correct: bool
    items: list[ItemResult]


def _check_item(gold: GoldCheck, spec: DesignSpec) -> tuple[bool, str]:
    if isinstance(gold, PatternRequired):
        hits = set(gold.acceptable) & set(spec.patterns)
        return bool(hits), (
            f"found {sorted(hits)}" if hits else f"none of {gold.acceptable} selected"
        )
    if isinstance(gold, PatternForbidden):
        hits = set(gold.forbidden) & set(spec.patterns)
        return not hits, (
            f"forbidden pattern(s) selected: {sorted(hits)}" if hits else "no forbidden pattern selected"
        )
    if isinstance(gold, SlotMatch):
        actual = spec.flow_decisions.get(gold.flow, {}).get(gold.slot)
        ok = actual in gold.acceptable
        return ok, f"{gold.flow}.{gold.slot} = {actual!r} (acceptable: {gold.acceptable})"
    if isinstance(gold, RoleRequired):
        hits = [c.component for c in spec.components
                if c.role in gold.acceptable_roles and c.component in gold.acceptable_components]
        return bool(hits), (
            f"role in {gold.acceptable_roles} filled by {hits}" if hits
            else f"no role in {gold.acceptable_roles} filled by any of {gold.acceptable_components}"
        )
    if isinstance(gold, RoleForbidden):
        hits = [c.component for c in spec.components
                if c.role == gold.role and c.component in gold.forbidden_components]
        return not hits, (
            f"forbidden assignment: {hits} as '{gold.role}'" if hits
            else f"no forbidden component assigned role '{gold.role}'"
        )
    if isinstance(gold, CoverageCheck):
        mapped = set(spec.coverage_map.get(gold.requirement, []))
        # a mapping only counts if the choice is actually part of the design
        selected = set(spec.patterns) | {c.component for c in spec.components}
        hits = mapped & set(gold.acceptable) & selected
        unselected = (mapped & set(gold.acceptable)) - selected
        if hits:
            return True, f"requirement '{gold.requirement}' covered by {sorted(hits)}"
        if unselected:
            return False, (
                f"requirement '{gold.requirement}' mapped to {sorted(unselected)}, "
                "but those choices are not part of the submitted design"
            )
        return False, (
            f"requirement '{gold.requirement}' not mapped to any of {gold.acceptable} (got {sorted(mapped)})"
        )
    if isinstance(gold, ParsimonyCheck):
        n = len(set(spec.patterns))
        return n <= gold.max_patterns, f"{n} distinct patterns selected (cap {gold.max_patterns})"
    if isinstance(gold, ConflictingConstraintsCheck):
        actual, expected = set(spec.conflicting_constraints), set(gold.expected)
        return actual == expected, f"named {sorted(actual)} (expected exactly {sorted(expected)})"
    if isinstance(gold, RelaxationCheck):
        ok = spec.proposed_relaxation in gold.acceptable
        return ok, f"proposed {spec.proposed_relaxation!r} (acceptable: {gold.acceptable})"
    if isinstance(gold, MissingInfoCheck):
        actual = set(spec.missing_info)
        missing_required = set(gold.required) - actual
        extras = actual - set(gold.allowed)
        ok = not missing_required and not extras
        parts = []
        if missing_required:
            parts.append(f"did not ask for required {sorted(missing_required)}")
        if extras:
            parts.append(f"asked for unjustified {sorted(extras)}")
        return ok, "; ".join(parts) if parts else f"asked exactly for {sorted(actual)}"
    raise TypeError(f"unhandled gold check type: {type(gold).__name__}")  # pragma: no cover


def grade_spec(scenario: Scenario, spec: DesignSpec | None) -> ScenarioResult:
    if spec is None:
        return ScenarioResult(
            instance_id=scenario.instance_id,
            format_compliant=False,
            expected_mode=scenario.expected_mode,
            actual_mode=None,
            mode_correct=False,
            items=[
                ItemResult(gold_id=g.id, dimension=g.dimension, check=g.check,
                           weight=g.weight, passed=False,
                           detail="no valid design spec submitted")
                for g in scenario.gold
            ],
        )

    mode_correct = spec.mode == scenario.expected_mode
    items: list[ItemResult] = []
    for gold in scenario.gold:
        if not mode_correct:
            items.append(ItemResult(
                gold_id=gold.id, dimension=gold.dimension, check=gold.check,
                weight=gold.weight, passed=False,
                detail=f"mode mismatch: submitted '{spec.mode.value}', expected '{scenario.expected_mode.value}'",
            ))
            continue
        passed, detail = _check_item(gold, spec)
        items.append(ItemResult(gold_id=gold.id, dimension=gold.dimension, check=gold.check,
                                weight=gold.weight, passed=passed, detail=detail))

    return ScenarioResult(
        instance_id=scenario.instance_id,
        format_compliant=True,
        expected_mode=scenario.expected_mode,
        actual_mode=spec.mode,
        mode_correct=mode_correct,
        items=items,
    )
