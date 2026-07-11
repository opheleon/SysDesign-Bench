"""Runner tests with a fake completion function — no network, no API keys."""
import json

from sdbench.prompt import build_prompt
from sdbench.runner import extract_json, parse_spec, run_scenario, run_scenarios

from factories import design_scenario, infeasible_gold_spec


GOLD_JSON = infeasible_gold_spec().model_dump_json()


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


class TestRunScenario:
    def test_compliant_first_try(self, proof_scenario, catalogs):
        calls = []

        def fake_complete(model, messages, temperature):
            calls.append(messages)
            return GOLD_JSON

        record = run_scenario(proof_scenario, catalogs, "fake-model", fake_complete)
        assert record.format_compliant
        assert not record.repair_attempted
        assert len(calls) == 1
        assert record.spec.proposed_relaxation == "RX2"

    def test_repair_retry_recovers(self, proof_scenario, catalogs):
        outputs = iter(["Sorry, here is prose instead of JSON.", GOLD_JSON])
        seen_messages = []

        def fake_complete(model, messages, temperature):
            seen_messages.append(list(messages))
            return next(outputs)

        record = run_scenario(proof_scenario, catalogs, "fake-model", fake_complete)
        assert record.format_compliant
        assert record.repair_attempted
        # repair call carries the previous output and the error feedback
        assert len(seen_messages) == 2
        assert seen_messages[1][1]["role"] == "assistant"
        assert "not a valid design spec" in seen_messages[1][2]["content"]

    def test_double_failure_is_noncompliant(self, proof_scenario, catalogs):
        def fake_complete(model, messages, temperature):
            return "still prose"

        record = run_scenario(proof_scenario, catalogs, "fake-model", fake_complete)
        assert not record.format_compliant
        assert record.repair_attempted
        assert record.spec is None
        assert record.error

    def test_run_scenarios_roundtrip(self, proof_scenario, catalogs, tmp_path):
        from sdbench.runner import read_predictions, write_predictions

        def fake_complete(model, messages, temperature):
            return GOLD_JSON

        records = run_scenarios([proof_scenario], catalogs, "fake-model", fake_complete)
        path = tmp_path / "predictions.jsonl"
        write_predictions(records, path)
        loaded = read_predictions(path)
        assert loaded == records


class TestPrompt:
    def test_prompt_carries_everything_needed(self, proof_scenario, catalogs):
        prompt = build_prompt(proof_scenario, catalogs)
        # constraint and relaxation ids the model must reference
        for needed in ("C1", "C4", "RX1", "RX3"):
            assert needed in prompt
        # catalogs present
        for needed in ("cache-aside", "postgresql-16", "system-of-record", "expected_write_qps"):
            assert needed in prompt
        # response contract present
        assert '"mode"' in prompt

    def test_prompt_includes_flow_slots(self, catalogs):
        prompt = build_prompt(design_scenario(), catalogs)
        assert "reserve" in prompt
        assert "consistency" in prompt
        assert "strong" in prompt
