"""Model execution: scenarios -> predictions.jsonl.

Generation is decoupled from grading (SWE-bench convention): this module's only
job is to produce a predictions file; `grade.py` never makes an LLM call.

Format handling follows the BFCL convention: if the model's output does not parse
into a valid DesignSpec, one repair attempt is made (feeding back the validation
error); after that the prediction is recorded as non-compliant and will score 0.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from .prompt import build_prompt
from .schema import Catalogs, DesignSpec, Scenario

# complete(model, messages, temperature) -> assistant text
CompleteFn = Callable[[str, list[dict], float], str]

REPAIR_TEMPLATE = (
    "Your previous response was not a valid design spec: {error}\n"
    "Respond again with ONLY the corrected JSON object, nothing else."
)


class PredictionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    instance_id: str
    model: str
    raw_output: str
    spec: DesignSpec | None
    format_compliant: bool
    repair_attempted: bool
    error: str = ""


def extract_json(text: str) -> str | None:
    """Return the first balanced top-level JSON object in `text`, or None.

    Tolerates prose or code fences around the object; string escapes are honored.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_spec(text: str) -> tuple[DesignSpec | None, str]:
    blob = extract_json(text)
    if blob is None:
        return None, "no JSON object found in response"
    try:
        return DesignSpec.model_validate(json.loads(blob)), ""
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except ValidationError as exc:
        return None, f"schema violation: {exc}"


def make_litellm_complete(
    api_base: str | None = None,
    api_key_env: str | None = None,
) -> CompleteFn:
    """Build a litellm completion function, optionally targeting an
    OpenAI-compatible endpoint (e.g. Baseten) with its own key env var."""

    def complete(model: str, messages: list[dict], temperature: float) -> str:
        try:
            import litellm  # noqa: PLC0415 — optional dependency, only needed for `run`
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "litellm is required to run models: pip install 'sdbench[run]'"
            ) from exc
        # Some models reject non-default sampling params (e.g. claude-sonnet-5
        # only supports temperature=1); drop unsupported params rather than
        # failing the run.
        litellm.drop_params = True
        kwargs: dict = {}
        if api_base:
            kwargs["api_base"] = api_base
        if api_key_env:
            key = os.environ.get(api_key_env)
            if not key:
                raise RuntimeError(f"environment variable {api_key_env} is not set")
            kwargs["api_key"] = key
        try:
            response = litellm.completion(
                model=model, messages=messages, temperature=temperature, **kwargs
            )
        except Exception as exc:
            # Models newer than litellm's registry can slip past drop_params and
            # reject non-default temperature (e.g. reasoning models pinned to 1).
            # Retry once at the provider default; the report's protocol note
            # covers this: temperature 0 where supported, provider default otherwise.
            if "temperature" not in str(exc):
                raise
            response = litellm.completion(model=model, messages=messages, **kwargs)
        return response.choices[0].message.content or ""

    return complete


litellm_complete = make_litellm_complete()


def run_scenario(
    scenario: Scenario,
    catalogs: Catalogs,
    model: str,
    complete_fn: CompleteFn = litellm_complete,
    temperature: float = 0.0,
) -> PredictionRecord:
    prompt = build_prompt(scenario, catalogs)
    messages: list[dict] = [{"role": "user", "content": prompt}]

    raw = complete_fn(model, messages, temperature)
    spec, error = parse_spec(raw)
    repair_attempted = False

    if spec is None:
        repair_attempted = True
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": REPAIR_TEMPLATE.format(error=error)},
        ]
        raw = complete_fn(model, messages, temperature)
        spec, error = parse_spec(raw)

    return PredictionRecord(
        instance_id=scenario.instance_id,
        model=model,
        raw_output=raw,
        spec=spec,
        format_compliant=spec is not None,
        repair_attempted=repair_attempted,
        error=error,
    )


def run_scenarios(
    scenarios: list[Scenario],
    catalogs: Catalogs,
    model: str,
    complete_fn: CompleteFn = litellm_complete,
    temperature: float = 0.0,
    on_progress: Callable[[PredictionRecord], None] | None = None,
) -> list[PredictionRecord]:
    records = []
    for scenario in scenarios:
        record = run_scenario(scenario, catalogs, model, complete_fn, temperature)
        records.append(record)
        if on_progress:
            on_progress(record)
    return records


def write_predictions(records: list[PredictionRecord], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")


def read_predictions(path: Path) -> list[PredictionRecord]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(PredictionRecord.model_validate_json(line))
    return records
