"""Pydantic models for SysDesign-Bench catalogs, scenarios, and design specs.

Everything gradable is expressed as typed set checks — if a criterion cannot be
represented by one of the GoldCheck types below, it is not a valid benchmark item.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Mode(str, Enum):
    DESIGN = "design"
    INFEASIBLE = "infeasible"
    CLARIFY = "clarify"


class Dimension(str, Enum):
    REQUIREMENTS_COVERAGE = "requirements_coverage"
    TRADEOFF_SELECTION = "tradeoff_selection"
    INFEASIBILITY_DETECTION = "infeasibility_detection"
    CLARIFICATION_SEEKING = "clarification_seeking"
    MIGRATION_SAFETY = "migration_safety"
    OPERABILITY = "operability"
    PARSIMONY = "parsimony"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Split(str, Enum):
    PUBLIC = "public"
    HELDOUT = "heldout"


class ConstraintType(str, Enum):
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    CONSISTENCY = "consistency"
    AVAILABILITY = "availability"
    DURABILITY = "durability"
    BUDGET = "budget"
    COMPLIANCE = "compliance"
    TEAM = "team"
    TOPOLOGY = "topology"
    NETWORK = "network"
    SCALE = "scale"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------

class _CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str = ""


class Component(_CatalogEntry):
    category: str


class Pattern(_CatalogEntry):
    category: str


class Role(_CatalogEntry):
    pass


class MissingInfoKey(_CatalogEntry):
    pass


class Catalogs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    components: list[Component]
    patterns: list[Pattern]
    roles: list[Role]
    missing_info: list[MissingInfoKey]

    @property
    def component_ids(self) -> set[str]:
        return {c.id for c in self.components}

    @property
    def pattern_ids(self) -> set[str]:
        return {p.id for p in self.patterns}

    @property
    def role_ids(self) -> set[str]:
        return {r.id for r in self.roles}

    @property
    def missing_info_ids(self) -> set[str]:
        return {m.id for m in self.missing_info}

    @property
    def choice_ids(self) -> set[str]:
        """All ids a coverage map may reference: patterns and components."""
        return self.pattern_ids | self.component_ids


def load_catalogs(catalog_dir: Path) -> Catalogs:
    """Load the four catalog files from a directory."""
    def _read(name: str) -> dict:
        with open(catalog_dir / name, encoding="utf-8") as f:
            return yaml.safe_load(f)

    components = _read("components.yaml")
    patterns = _read("patterns.yaml")
    roles = _read("roles.yaml")
    missing_info = _read("missing_info.yaml")
    versions = {
        components["version"], patterns["version"],
        roles["version"], missing_info["version"],
    }
    if len(versions) != 1:
        raise ValueError(f"Catalog files disagree on version: {sorted(versions)}")
    return Catalogs(
        version=versions.pop(),
        components=components["components"],
        patterns=patterns["patterns"],
        roles=roles["roles"],
        missing_info=missing_info["missing_info"],
    )


# ---------------------------------------------------------------------------
# Scenario building blocks
# ---------------------------------------------------------------------------

class Constraint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: ConstraintType
    value: str
    stated_in_prompt: bool = True


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    implicit: bool = False


class Flow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    description: str
    decision_slots: dict[str, list[str]]  # slot name -> allowed option values


class RelaxationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    description: str


class Trap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    description: str
    violated_constraint_id: str
    why_attractive: str


class Twist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    invalidated_canonical: str
    twist_gold_refs: list[str]  # gold item ids a canonical recitation fails


# ---------------------------------------------------------------------------
# Gold checks (the entire scoring surface — every one is a set/enum check)
# ---------------------------------------------------------------------------

class _GoldBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    dimension: Dimension


class PatternRequired(_GoldBase):
    check: Literal["pattern_required"]
    acceptable: list[str]  # pass if at least one is in spec.patterns


class PatternForbidden(_GoldBase):
    check: Literal["pattern_forbidden"]
    forbidden: list[str]  # pass if none is in spec.patterns


class SlotMatch(_GoldBase):
    check: Literal["slot_match"]
    flow: str
    slot: str
    acceptable: list[str]  # pass if spec.flow_decisions[flow][slot] is in this set


class RoleRequired(_GoldBase):
    check: Literal["role_required"]
    acceptable_roles: list[str]        # pass if any component from the acceptable set
    acceptable_components: list[str]   # carries any of the acceptable roles


class RoleForbidden(_GoldBase):
    check: Literal["role_forbidden"]
    role: str
    forbidden_components: list[str]  # pass if no (component, role) pair matches


class CoverageCheck(_GoldBase):
    check: Literal["coverage"]
    requirement: str
    acceptable: list[str]  # pass if coverage_map[requirement] intersects this set


class ParsimonyCheck(_GoldBase):
    check: Literal["parsimony"]
    max_patterns: int  # pass if len(spec.patterns) <= max_patterns


class ConflictingConstraintsCheck(_GoldBase):
    check: Literal["conflicting_constraints"]
    expected: list[str]  # pass on set equality with spec.conflicting_constraints


class RelaxationCheck(_GoldBase):
    check: Literal["relaxation"]
    acceptable: list[str]  # pass if spec.proposed_relaxation is in this set


class MissingInfoCheck(_GoldBase):
    check: Literal["missing_info"]
    required: list[str]  # every required key must be in spec.missing_info
    allowed: list[str]   # spec.missing_info must be a subset of this (superset of required)


GoldCheck = Annotated[
    Union[
        PatternRequired,
        PatternForbidden,
        SlotMatch,
        RoleRequired,
        RoleForbidden,
        CoverageCheck,
        ParsimonyCheck,
        ConflictingConstraintsCheck,
        RelaxationCheck,
        MissingInfoCheck,
    ],
    Field(discriminator="check"),
]


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    domain: str
    difficulty: Difficulty
    version: str
    split: Split

    problem_statement: str
    context: str = ""

    constraints: list[Constraint]
    requirements: list[Requirement]
    flows: list[Flow] = []
    relaxation_options: list[RelaxationOption] = []

    expected_mode: Mode
    gold: list[GoldCheck]
    traps: list[Trap]
    twist: Twist
    gold_rationale: str


def load_scenario(path: Path) -> Scenario:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Scenario.model_validate(data)


def load_scenarios(scenario_dir: Path) -> list[Scenario]:
    return [load_scenario(p) for p in sorted(scenario_dir.rglob("*.yaml"))]


# ---------------------------------------------------------------------------
# Design spec (the model's graded response)
# ---------------------------------------------------------------------------

class ComponentChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: str
    role: str


class DesignSpec(BaseModel):
    """The single JSON artifact a model submits per scenario.

    `notes` is never graded. Everything else is matched against gold sets.
    """
    model_config = ConfigDict(extra="forbid")

    mode: Mode

    # clarify mode
    missing_info: list[str] = []

    # infeasible mode
    conflicting_constraints: list[str] = []
    proposed_relaxation: str | None = None

    # design mode
    components: list[ComponentChoice] = []
    patterns: list[str] = []
    flow_decisions: dict[str, dict[str, str]] = {}
    coverage_map: dict[str, list[str]] = {}

    notes: str = ""
