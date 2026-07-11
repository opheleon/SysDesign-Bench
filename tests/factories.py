"""Synthetic scenarios and specs for harness tests.

These are deliberately small but validator-clean, so grader tests exercise the
same shapes real scenarios use. Kept out of conftest.py on purpose — conftest is
for fixtures only.
"""
from __future__ import annotations

from sdbench.schema import DesignSpec, Scenario

_RATIONALE_PAD = (
    "Worked math placeholder for synthetic test scenario: the constraint set pins "
    "the gold answer by construction, and each trap violates the named constraint. "
    "This padding exists to satisfy the minimum-rationale lint in tests. " * 2
)


def design_scenario() -> Scenario:
    """Design-mode scenario exercising every design-check type."""
    return Scenario.model_validate(
        {
            "instance_id": "synthetic-locker-001",
            "domain": "parcel-lockers",
            "difficulty": "easy",
            "version": "0.1.0",
            "split": "public",
            "problem_statement": "Reserve parcel lockers; retried reservation calls must be safe.",
            "context": "Modest scale: 200 lockers, low hundreds of reservations/day.",
            "constraints": [
                {"id": "C1", "type": "consistency", "value": "a locker is never double-reserved"},
                {"id": "C2", "type": "budget", "value": "minimal infrastructure; two-person team"},
            ],
            "requirements": [
                {"id": "R1", "text": "Reserve and release lockers."},
                {"id": "R2", "text": "Retried reservation requests must not double-book.", "implicit": False},
            ],
            "flows": [
                {
                    "id": "reserve",
                    "description": "reservation write path",
                    "decision_slots": {"consistency": ["strong", "eventual"]},
                }
            ],
            "relaxation_options": [],
            "expected_mode": "design",
            "gold": [
                {"id": "g1", "dimension": "operability", "check": "pattern_required",
                 "acceptable": ["idempotency-keys"]},
                {"id": "g2", "dimension": "tradeoff_selection", "check": "pattern_forbidden",
                 "forbidden": ["event-sourcing", "cqrs"]},
                {"id": "g3", "dimension": "tradeoff_selection", "check": "slot_match",
                 "flow": "reserve", "slot": "consistency", "acceptable": ["strong"]},
                {"id": "g4", "dimension": "tradeoff_selection", "check": "role_required",
                 "acceptable_roles": ["system-of-record"],
                 "acceptable_components": ["postgresql-16", "mysql-8"]},
                {"id": "g5", "dimension": "tradeoff_selection", "check": "role_forbidden",
                 "role": "system-of-record", "forbidden_components": ["redis-7", "memcached"]},
                {"id": "g6", "dimension": "requirements_coverage", "check": "coverage",
                 "requirement": "R2", "acceptable": ["idempotency-keys"]},
                {"id": "g7", "dimension": "parsimony", "check": "parsimony", "max_patterns": 4},
            ],
            "traps": [
                {"id": "T1", "description": "Event sourcing for a tiny CRUD reservation system",
                 "violated_constraint_id": "C2",
                 "why_attractive": "Reservations sound like events; the pattern is fashionable."},
            ],
            "twist": {
                "description": "Modest scale makes the fashionable answer wrong.",
                "invalidated_canonical": "Event-sourced booking-system architectures.",
                "twist_gold_refs": ["g2"],
            },
            "gold_rationale": _RATIONALE_PAD,
        }
    )


def clarify_scenario() -> Scenario:
    """Clarify-mode scenario: load numbers are deleted; designing anyway fails."""
    return Scenario.model_validate(
        {
            "instance_id": "synthetic-onsale-001",
            "domain": "ticketing",
            "difficulty": "medium",
            "version": "0.1.0",
            "split": "public",
            "problem_statement": "Build the on-sale service. Spikes expected; budget constrained.",
            "context": "Neither peak load nor the budget ceiling has been provided.",
            "constraints": [
                {"id": "C1", "type": "scale", "value": "spiky traffic, magnitude unstated", "stated_in_prompt": False},
                {"id": "C2", "type": "budget", "value": "capped, ceiling unstated", "stated_in_prompt": False},
            ],
            "requirements": [{"id": "R1", "text": "Sell tickets during announced on-sales."}],
            "flows": [],
            "relaxation_options": [],
            "expected_mode": "clarify",
            "gold": [
                {"id": "g1", "dimension": "clarification_seeking", "check": "missing_info",
                 "required": ["expected_write_qps", "budget"],
                 "allowed": ["expected_write_qps", "budget", "peak_to_average_ratio", "expected_read_qps"]},
            ],
            "traps": [
                {"id": "T1", "description": "Design anyway assuming a comfortable scale",
                 "violated_constraint_id": "C1",
                 "why_attractive": "The problem looks like a standard CRUD service."},
            ],
            "twist": {
                "description": "The familiar scenario arrives with its numbers deleted.",
                "invalidated_canonical": "Reciting the standard ticketing architecture without load figures.",
                "twist_gold_refs": ["g1"],
            },
            "gold_rationale": _RATIONALE_PAD,
        }
    )


def gold_design_spec() -> DesignSpec:
    """Passes every item of design_scenario()."""
    return DesignSpec.model_validate(
        {
            "mode": "design",
            "components": [
                {"component": "postgresql-16", "role": "system-of-record"},
                {"component": "python", "role": "implementation-language"},
            ],
            "patterns": ["idempotency-keys", "retries-with-backoff"],
            "flow_decisions": {"reserve": {"consistency": "strong"}},
            "coverage_map": {"R1": ["postgresql-16"], "R2": ["idempotency-keys"]},
        }
    )


def infeasible_gold_spec() -> DesignSpec:
    """Passes both items of the retail-inventory proof scenario."""
    return DesignSpec.model_validate(
        {
            "mode": "infeasible",
            "conflicting_constraints": ["C1", "C2", "C3", "C4"],
            "proposed_relaxation": "RX2",
            "notes": "WAN RTT physical floor exceeds the 9ms budget by an order of magnitude.",
        }
    )
