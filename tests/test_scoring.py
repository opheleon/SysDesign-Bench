from sdbench.grade import grade_spec
from sdbench.schema import Dimension
from sdbench.scoring import render_report, score

from factories import (
    clarify_scenario,
    design_scenario,
    gold_design_spec,
    infeasible_gold_spec,
)


def _results(proof_scenario):
    return [
        grade_spec(proof_scenario, infeasible_gold_spec()),
        grade_spec(design_scenario(), gold_design_spec()),
        grade_spec(clarify_scenario(), None),  # format failure
    ]


def test_score_aggregation(proof_scenario):
    scores = score(_results(proof_scenario), model="fake-model", benchmark_version="0.1.0")

    assert scores.n_scenarios == 3
    assert scores.format_compliance == 2 / 3
    assert scores.mode_accuracy == 2 / 3

    # perfect dimensions from the two graded scenarios
    assert scores.dimensions[Dimension.INFEASIBILITY_DETECTION].fraction == 1.0
    assert scores.dimensions[Dimension.PARSIMONY].fraction == 1.0
    # the clarify scenario's only item failed via format failure
    assert scores.dimensions[Dimension.CLARIFICATION_SEEKING].fraction == 0.0
    # headline is macro-average over dimensions, so the failed dimension drags it
    assert 0.0 < scores.overall < 1.0


def test_perfect_run_scores_one(proof_scenario):
    results = [
        grade_spec(proof_scenario, infeasible_gold_spec()),
        grade_spec(design_scenario(), gold_design_spec()),
    ]
    scores = score(results, model="fake-model", benchmark_version="0.1.0")
    assert scores.overall == 1.0
    assert scores.mode_accuracy == 1.0
    assert scores.format_compliance == 1.0


def test_report_renders(proof_scenario):
    scores = score(_results(proof_scenario), model="fake-model", benchmark_version="0.1.0")
    report = render_report([scores])
    assert "| Model |" in report
    assert "fake-model" in report
    assert "Failed items — fake-model" in report
    assert "synthetic-onsale-001" in report  # the failed clarify scenario shows up
