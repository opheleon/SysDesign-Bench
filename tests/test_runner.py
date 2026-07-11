"""Runner tests with fake completion functions — no network, no API keys."""
import json

from sdbench.prompt import build_prompt
from sdbench.providers import Completion, Usage, compute_cost, parse_model_spec
from sdbench.runner import extract_json, parse_spec, run_scenario, run_scenarios

from factories import design_scenario, infeasible_gold_spec


GOLD_JSON = infeasible_gold_spec().model_dump_json()
FAKE_USAGE = Usage(prompt_tokens=1000, completion_tokens=200, cost_usd=0.015)


def fake_complete_returning(*outputs: str):
    """Build a complete_fn yielding the given outputs in order, recording calls."""
    calls: list[list[dict]] = []
    iterator = iter(outputs)

    def complete(messages: list[dict]) -> Completion:
        calls.append(list(messages))
        return Completion(text=next(iterator), usage=FAKE_USAGE)

    complete.calls = calls
    return complete


class TestExtractJson:
    def test_bare_object(self):
        assert extract_json('{"mode": "design"}') == '{"mode": "design"}'

    def test_fenced_object(self):
        text = 'Here is my answer:\n```json\n{"mode": "clarify", "missing_info": []}\n```\nThanks!'
        assert json.loads(extract_json(text))["mode"] == "clarify"

    def test_braces_inside_strings_ignored(self):
        text = '{"mode": "design", "notes": "uses {curly} braces and a \\" quote"}'
        assert extract_json(text) == text

    def test_no_object(self):
        assert extract_json("I cannot answer in JSON.") is None

    def test_unbalanced_object(self):
        assert extract_json('{"mode": "design"') is None


class TestParseSpec:
    def test_valid(self):
        spec, error = parse_spec(GOLD_JSON)
        assert spec is not None and error == ""

    def test_schema_violation_reported(self):
        spec, error = parse_spec('{"mode": "design", "surprise": 1}')
        assert spec is None
        assert "schema violation" in error

    def test_bad_mode_reported(self):
        spec, error = parse_spec('{"mode": "essay"}')
        assert spec is None


class TestProviders:
    def test_parse_model_spec(self):
        assert parse_model_spec("anthropic/claude-fable-5") == ("anthropic", "claude-fable-5")
        assert parse_model_spec("baseten/zai-org/GLM-5.2") == ("baseten", "zai-org/GLM-5.2")

    def test_compute_cost(self):
        # fable-5: $10/M in, $50/M out
        assert compute_cost("claude-fable-5", 1_000_000, 100_000) == 10.0 + 5.0
        assert compute_cost("unknown-model", 1000, 1000) == 0.0


class TestRunScenario:
    def test_compliant_first_try(self, proof_scenario, catalogs):
        fake = fake_complete_returning(GOLD_JSON)
        record = run_scenario(proof_scenario, catalogs, "fake/model", fake)
        assert record.format_compliant
        assert not record.repair_attempted
        assert len(fake.calls) == 1
        assert record.spec.proposed_relaxation == "RX2"
        assert record.prompt_tokens == 1000
        assert record.cost_usd == 0.015

    def test_repair_retry_recovers(self, proof_scenario, catalogs):
        fake = fake_complete_returning("Sorry, here is prose instead of JSON.", GOLD_JSON)
        record = run_scenario(proof_scenario, catalogs, "fake/model", fake)
        assert record.format_compliant
        assert record.repair_attempted
        # usage accumulates across both calls
        assert record.prompt_tokens == 2000
        assert record.cost_usd == 0.03
        # repair call carries the previous output and the error feedback
        assert len(fake.calls) == 2
        assert fake.calls[1][1]["role"] == "assistant"
        assert "not a valid design spec" in fake.calls[1][2]["content"]

    def test_double_failure_is_noncompliant(self, proof_scenario, catalogs):
        fake = fake_complete_returning("still prose", "yet more prose")
        record = run_scenario(proof_scenario, catalogs, "fake/model", fake)
        assert not record.format_compliant
        assert record.repair_attempted
        assert record.spec is None
        assert record.error

    def test_provider_exception_fails_scenario_not_run(self, proof_scenario, catalogs):
        def exploding(messages):
            raise TimeoutError("request timed out after 600s")

        record = run_scenario(proof_scenario, catalogs, "fake/model", exploding)
        assert not record.format_compliant
        assert "provider error" in record.error
        assert "timed out" in record.error

    def test_resume_skips_existing(self, proof_scenario, catalogs):
        fake = fake_complete_returning(GOLD_JSON)
        records = run_scenarios(
            [proof_scenario], catalogs, "fake/model", fake,
            skip_instance_ids={proof_scenario.instance_id},
        )
        assert records == []
        assert fake.calls == []

    def test_run_scenarios_roundtrip(self, proof_scenario, catalogs, tmp_path):
        from sdbench.runner import read_predictions, write_predictions

        fake = fake_complete_returning(GOLD_JSON)
        records = run_scenarios([proof_scenario], catalogs, "fake/model", fake)
        path = tmp_path / "predictions.jsonl"
        write_predictions(records, path)
        assert read_predictions(path) == records

    def test_append_prediction_incremental(self, proof_scenario, catalogs, tmp_path):
        from sdbench.runner import append_prediction, read_predictions

        fake = fake_complete_returning(GOLD_JSON, GOLD_JSON)
        path = tmp_path / "predictions.jsonl"
        run_scenarios(
            [proof_scenario], catalogs, "fake/model", fake,
            on_progress=lambda r: append_prediction(r, path),
        )
        assert len(read_predictions(path)) == 1


class TestPrompt:
    def test_prompt_carries_everything_needed(self, proof_scenario, catalogs):
        prompt = build_prompt(proof_scenario, catalogs)
        for needed in ("C1", "C4", "RX1", "RX3"):
            assert needed in prompt
        for needed in ("cache-aside", "postgresql-16", "system-of-record", "expected_write_qps"):
            assert needed in prompt
        assert '"mode"' in prompt

    def test_prompt_includes_flow_slots(self, catalogs):
        prompt = build_prompt(design_scenario(), catalogs)
        assert "reserve" in prompt
        assert "consistency" in prompt
        assert "strong" in prompt
