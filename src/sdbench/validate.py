"""Scenario and catalog linter.

Enforces the benchmark's authoring bar: every referenced id exists, every trap
names the constraint it violates, every twist invalidates at least one gold item,
and each expected mode carries the gold checks that make it gradable. A scenario
that fails validation is not ready to score.
"""
from __future__ import annotations

from collections import Counter

from .schema import (
    Catalogs,
    ConflictingConstraintsCheck,
    CoverageCheck,
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

MIN_GOLD_RATIONALE_CHARS = 200

DESIGN_CHECKS = (
    PatternRequired, PatternForbidden, SlotMatch,
    RoleRequired, RoleForbidden, CoverageCheck, ParsimonyCheck,
)
INFEASIBLE_CHECKS = (ConflictingConstraintsCheck, RelaxationCheck)
CLARIFY_CHECKS = (MissingInfoCheck,)


def _duplicates(ids: list[str]) -> list[str]:
    return sorted(i for i, n in Counter(ids).items() if n > 1)


def validate_catalogs(catalogs: Catalogs) -> list[str]:
    errors: list[str] = []
    for label, ids in (
        ("component", [c.id for c in catalogs.components]),
        ("pattern", [p.id for p in catalogs.patterns]),
        ("role", [r.id for r in catalogs.roles]),
        ("missing_info", [m.id for m in catalogs.missing_info]),
    ):
        for dup in _duplicates(ids):
            errors.append(f"catalogs: duplicate {label} id '{dup}'")
    return errors


def validate_scenario(scenario: Scenario, catalogs: Catalogs) -> list[str]:  # noqa: C901
    errors: list[str] = []
    sid = scenario.instance_id

    def err(msg: str) -> None:
        errors.append(f"{sid}: {msg}")

    constraint_ids = {c.id for c in scenario.constraints}
    requirement_ids = {r.id for r in scenario.requirements}
    relaxation_ids = {r.id for r in scenario.relaxation_options}
    flow_slots = {f.id: f.decision_slots for f in scenario.flows}
    gold_ids = [g.id for g in scenario.gold]

    # --- unique ids everywhere ---
    for label, ids in (
        ("constraint", [c.id for c in scenario.constraints]),
        ("requirement", [r.id for r in scenario.requirements]),
        ("flow", [f.id for f in scenario.flows]),
        ("relaxation option", [r.id for r in scenario.relaxation_options]),
        ("gold item", gold_ids),
        ("trap", [t.id for t in scenario.traps]),
    ):
        for dup in _duplicates(ids):
            err(f"duplicate {label} id '{dup}'")

    # --- gold checks reference real catalog / scenario entities ---
    for g in scenario.gold:
        where = f"gold item '{g.id}'"
        if isinstance(g, PatternRequired):
            for pid in set(g.acceptable) - catalogs.pattern_ids:
                err(f"{where}: unknown pattern '{pid}'")
            if not g.acceptable:
                err(f"{where}: empty acceptable set")
        elif isinstance(g, PatternForbidden):
            for pid in set(g.forbidden) - catalogs.pattern_ids:
                err(f"{where}: unknown pattern '{pid}'")
            if not g.forbidden:
                err(f"{where}: empty forbidden set")
        elif isinstance(g, SlotMatch):
            if g.flow not in flow_slots:
                err(f"{where}: unknown flow '{g.flow}'")
            elif g.slot not in flow_slots[g.flow]:
                err(f"{where}: flow '{g.flow}' has no slot '{g.slot}'")
            else:
                allowed = set(flow_slots[g.flow][g.slot])
                for v in set(g.acceptable) - allowed:
                    err(f"{where}: acceptable value '{v}' not among the slot's allowed options")
            if not g.acceptable:
                err(f"{where}: empty acceptable set")
        elif isinstance(g, RoleRequired):
            for rid in set(g.acceptable_roles) - catalogs.role_ids:
                err(f"{where}: unknown role '{rid}'")
            for cid in set(g.acceptable_components) - catalogs.component_ids:
                err(f"{where}: unknown component '{cid}'")
            if not g.acceptable_roles:
                err(f"{where}: empty acceptable_roles set")
            if not g.acceptable_components:
                err(f"{where}: empty acceptable_components set")
        elif isinstance(g, RoleForbidden):
            if g.role not in catalogs.role_ids:
                err(f"{where}: unknown role '{g.role}'")
            for cid in set(g.forbidden_components) - catalogs.component_ids:
                err(f"{where}: unknown component '{cid}'")
            if not g.forbidden_components:
                err(f"{where}: empty forbidden_components set")
        elif isinstance(g, CoverageCheck):
            if g.requirement not in requirement_ids:
                err(f"{where}: unknown requirement '{g.requirement}'")
            for cid in set(g.acceptable) - catalogs.choice_ids:
                err(f"{where}: unknown pattern/component '{cid}'")
            if not g.acceptable:
                err(f"{where}: empty acceptable set")
        elif isinstance(g, ParsimonyCheck):
            if g.max_patterns < 1:
                err(f"{where}: max_patterns must be >= 1")
        elif isinstance(g, ConflictingConstraintsCheck):
            for cid in set(g.expected) - constraint_ids:
                err(f"{where}: unknown constraint '{cid}'")
            if len(set(g.expected)) < 2:
                err(f"{where}: infeasibility requires >= 2 conflicting constraint ids")
        elif isinstance(g, RelaxationCheck):
            for rid in set(g.acceptable) - relaxation_ids:
                err(f"{where}: unknown relaxation option '{rid}'")
            if not g.acceptable:
                err(f"{where}: empty acceptable set")
        elif isinstance(g, MissingInfoCheck):
            for key in set(g.required) - catalogs.missing_info_ids:
                err(f"{where}: unknown missing_info key '{key}' in required")
            for key in set(g.allowed) - catalogs.missing_info_ids:
                err(f"{where}: unknown missing_info key '{key}' in allowed")
            if not set(g.required) <= set(g.allowed):
                err(f"{where}: required keys must be a subset of allowed keys")
            if not g.required:
                err(f"{where}: empty required set")

    # --- expected mode carries the checks that make it gradable ---
    mode_requirements = {
        Mode.DESIGN: (DESIGN_CHECKS, "at least one design check (pattern/slot/role/coverage/parsimony)"),
        Mode.INFEASIBLE: (INFEASIBLE_CHECKS, "a conflicting_constraints check and a relaxation check"),
        Mode.CLARIFY: (CLARIFY_CHECKS, "a missing_info check"),
    }
    expected_types, description = mode_requirements[scenario.expected_mode]
    if not any(isinstance(g, expected_types) for g in scenario.gold):
        err(f"expected_mode '{scenario.expected_mode.value}' but gold has no matching check ({description})")
    if scenario.expected_mode == Mode.INFEASIBLE:
        if not any(isinstance(g, ConflictingConstraintsCheck) for g in scenario.gold):
            err("infeasible scenario missing a conflicting_constraints gold check")
        if not any(isinstance(g, RelaxationCheck) for g in scenario.gold):
            err("infeasible scenario missing a relaxation gold check")
        if not scenario.relaxation_options:
            err("infeasible scenario must enumerate relaxation_options")

    # --- traps ---
    if not scenario.traps:
        err("at least one trap is required")
    for t in scenario.traps:
        if t.violated_constraint_id not in constraint_ids:
            err(f"trap '{t.id}': unknown violated_constraint_id '{t.violated_constraint_id}'")

    # --- twist must invalidate something ---
    if not scenario.twist.twist_gold_refs:
        err("twist must reference at least one gold item a canonical recitation fails")
    for ref in set(scenario.twist.twist_gold_refs) - set(gold_ids):
        err(f"twist references unknown gold item '{ref}'")

    # --- gold rationale is the review artifact; it cannot be a stub ---
    if len(scenario.gold_rationale.strip()) < MIN_GOLD_RATIONALE_CHARS:
        err(
            f"gold_rationale too short ({len(scenario.gold_rationale.strip())} chars, "
            f"minimum {MIN_GOLD_RATIONALE_CHARS}) — it must contain the worked constraint math"
        )

    return errors
