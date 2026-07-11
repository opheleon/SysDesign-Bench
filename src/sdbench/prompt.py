"""Canonical prompt template.

One fixed template for every scenario and every model — part of the pinned eval
protocol. The prompt carries the full global catalogs verbatim (they are identical
across scenarios, so they leak nothing) plus the scenario's numbered constraints,
requirements, flows, and relaxation options.
"""
from __future__ import annotations

from .schema import Catalogs, Scenario

RESPONSE_INSTRUCTIONS = """\
## Your response

Respond with ONLY one JSON object (no prose before or after) with this shape:

{
  "mode": "design" | "infeasible" | "clarify",

  // mode == "clarify": the information you need before you can design responsibly.
  "missing_info": ["<missing-info key>", ...],

  // mode == "infeasible": which constraints jointly cannot be satisfied, and the
  // one relaxation option you recommend.
  "conflicting_constraints": ["<constraint id>", ...],
  "proposed_relaxation": "<relaxation option id>",

  // mode == "design":
  "components": [{"component": "<component id>", "role": "<role id>"}, ...],
  "patterns": ["<pattern id>", ...],
  "flow_decisions": {"<flow id>": {"<slot>": "<chosen option>", ...}, ...},
  "coverage_map": {"<requirement id>": ["<pattern or component id>", ...], ...},

  // optional, never graded
  "notes": "<free text>"
}

Rules:
- Pick exactly one mode. Use "clarify" only if information critical to the design
  decision is genuinely missing. Use "infeasible" only if the stated constraints
  cannot be jointly satisfied by any design — in that case list the exact
  conflicting constraint ids and pick one relaxation option id.
- In "design" mode: every component, pattern, and role id must come from the
  catalogs below; fill every decision slot of every flow; map every requirement
  id in coverage_map to the choice(s) that address it. Select only what the
  requirements and constraints justify — unnecessary selections count against you.
- Ids are case-sensitive. Do not invent ids.
"""


def _catalog_section(catalogs: Catalogs) -> str:
    lines: list[str] = ["## Catalogs (choose only from these ids)", ""]
    lines.append("### Components")
    for c in catalogs.components:
        lines.append(f"- {c.id} ({c.category}): {c.description}")
    lines.append("")
    lines.append("### Patterns")
    for p in catalogs.patterns:
        lines.append(f"- {p.id} ({p.category}): {p.description}")
    lines.append("")
    lines.append("### Roles")
    for r in catalogs.roles:
        lines.append(f"- {r.id}: {r.description}")
    lines.append("")
    lines.append("### Missing-info keys (for clarify mode)")
    for m in catalogs.missing_info:
        lines.append(f"- {m.id}: {m.description}")
    return "\n".join(lines)


def _scenario_section(scenario: Scenario) -> str:
    lines: list[str] = ["## Problem", "", scenario.problem_statement.strip()]
    if scenario.context.strip():
        lines += ["", "## Context", "", scenario.context.strip()]

    lines += ["", "## Constraints"]
    for c in scenario.constraints:
        lines.append(f"- {c.id} ({c.type.value}): {c.value}")

    lines += ["", "## Requirements"]
    for r in scenario.requirements:
        lines.append(f"- {r.id}: {r.text}")

    if scenario.flows:
        lines += ["", "## Flows and decision slots (fill every slot in design mode)"]
        for f in scenario.flows:
            lines.append(f"- {f.id}: {f.description}")
            for slot, options in f.decision_slots.items():
                lines.append(f"  - {slot}: one of {options}")

    if scenario.relaxation_options:
        lines += ["", "## Relaxation options (only relevant if you declare infeasibility)"]
        for rx in scenario.relaxation_options:
            lines.append(f"- {rx.id}: {rx.description}")

    return "\n".join(lines)


def build_prompt(scenario: Scenario, catalogs: Catalogs) -> str:
    header = (
        "You are designing a software system. Read the problem, constraints, and "
        "requirements, then respond with a single structured design spec JSON.\n"
    )
    return "\n\n".join(
        [
            header,
            _scenario_section(scenario),
            _catalog_section(catalogs),
            RESPONSE_INSTRUCTIONS,
        ]
    )
