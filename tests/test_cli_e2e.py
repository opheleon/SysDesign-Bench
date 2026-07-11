"""End-to-end CLI test: predictions.jsonl -> sdbench grade -> sdbench report.

Exercises the decoupled flow exactly as a third party would use it, with a
predictions file written by the runner (fake model) — zero LLM calls.
"""
import json

from sdbench.cli import main
from sdbench.runner import run_scenarios, write_predictions

from conftest import CATALOG_DIR, SCENARIO_DIR
from factories import infeasible_gold_spec


def _write_predictions(path, catalogs, proof_scenario, response_json):
    records = run_scenarios(
        [proof_scenario], catalogs, "fake-model",
        complete_fn=lambda model, messages, temperature: response_json,
    )
    write_predictions(records, path)


def test_grade_and_report_e2e(tmp_path, catalogs, proof_scenario, capsys):
    predictions = tmp_path / "predictions.jsonl"
    scores_path = tmp_path / "scores.json"
    _write_predictions(predictions, catalogs, proof_scenario, infeasible_gold_spec().model_dump_json())

    rc = main([
        "grade", str(predictions),
        "--catalogs", str(CATALOG_DIR),
        "--scenarios", str(SCENARIO_DIR),
        "--out", str(scores_path),
    ])
    assert rc == 0

    data = json.loads(scores_path.read_text())
    assert len(data) == 1
    assert data[0]["model"] == "fake-model"
    assert data[0]["overall"] == 1.0
    assert data[0]["format_compliance"] == 1.0

    rc = main(["report", str(scores_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fake-model" in out
    assert "| Model |" in out


def test_grade_trap_answer_scores_partial(tmp_path, catalogs, proof_scenario):
    predictions = tmp_path / "predictions.jsonl"
    scores_path = tmp_path / "scores.json"
    trap_spec = infeasible_gold_spec().model_copy(update={"proposed_relaxation": "RX4"})
    _write_predictions(predictions, catalogs, proof_scenario, trap_spec.model_dump_json())

    rc = main([
        "grade", str(predictions),
        "--catalogs", str(CATALOG_DIR),
        "--scenarios", str(SCENARIO_DIR),
        "--out", str(scores_path),
    ])
    assert rc == 0
    data = json.loads(scores_path.read_text())
    # g1 passes, g2 (relaxation) fails -> infeasibility_detection = 0.5
    assert data[0]["dimensions"]["infeasibility_detection"]["passed"] == 1
    assert data[0]["dimensions"]["infeasibility_detection"]["total"] == 2


def test_validate_cli(capsys):
    rc = main([
        "validate",
        "--catalogs", str(CATALOG_DIR),
        "--scenarios", str(SCENARIO_DIR),
    ])
    assert rc == 0
    assert "0 error(s)" in capsys.readouterr().out
