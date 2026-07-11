"""Score aggregation and reporting.

Per-dimension score = fraction of that dimension's gold items passed across all
scenarios. Headline = macro-average over dimensions that have items. Format
compliance and mode accuracy are reported alongside, never blended in.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .grade import ScenarioResult
from .schema import Dimension


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: int
    total: int

    @property
    def fraction(self) -> float:
        return self.passed / self.total if self.total else 0.0


class RunScores(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    model: str
    benchmark_version: str
    n_scenarios: int
    format_compliance: float
    mode_accuracy: float
    dimensions: dict[Dimension, DimensionScore]
    overall: float
    scenarios: list[ScenarioResult]


def score(results: list[ScenarioResult], model: str, benchmark_version: str) -> RunScores:
    dimensions: dict[Dimension, DimensionScore] = {}
    for result in results:
        for item in result.items:
            bucket = dimensions.setdefault(item.dimension, DimensionScore(passed=0, total=0))
            bucket.total += 1
            bucket.passed += int(item.passed)

    n = len(results)
    overall = (
        sum(d.fraction for d in dimensions.values()) / len(dimensions) if dimensions else 0.0
    )
    return RunScores(
        model=model,
        benchmark_version=benchmark_version,
        n_scenarios=n,
        format_compliance=sum(r.format_compliant for r in results) / n if n else 0.0,
        mode_accuracy=sum(r.mode_correct for r in results) / n if n else 0.0,
        dimensions=dimensions,
        overall=overall,
        scenarios=results,
    )


def render_report(all_scores: list[RunScores]) -> str:
    """Markdown leaderboard table plus per-model failed-item detail."""
    dims = sorted({d for s in all_scores for d in s.dimensions}, key=lambda d: d.value)
    header = ["Model", "Overall"] + [d.value for d in dims] + ["Mode acc.", "Format"]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for s in sorted(all_scores, key=lambda s: s.overall, reverse=True):
        row = [s.model, f"{s.overall:.2f}"]
        for d in dims:
            row.append(f"{s.dimensions[d].fraction:.2f}" if d in s.dimensions else "—")
        row += [f"{s.mode_accuracy:.2f}", f"{s.format_compliance:.2f}"]
        lines.append("| " + " | ".join(row) + " |")

    for s in all_scores:
        failed = [
            (r.instance_id, i)
            for r in s.scenarios for i in r.items if not i.passed
        ]
        if failed:
            lines += ["", f"### Failed items — {s.model}", ""]
            for instance_id, item in failed:
                lines.append(f"- `{instance_id}` / `{item.gold_id}` ({item.dimension.value}): {item.detail}")
    return "\n".join(lines)
