"""Model execution: scenarios -> predictions.jsonl.

Generation is decoupled from grading (SWE-bench convention): this module's only
job is to produce a predictions file; `grade.py` never makes an LLM call.

Reliability contract:
- every request carries a hard timeout (providers.py) — a hung call cannot
  stall a run;
- a provider exception fails THAT scenario's prediction, not the run;
- predictions are appended incrementally as they complete, so a killed run
  loses at most the in-flight scenario and can be resumed (`--resume`).

Format handling follows the BFCL convention: if the model's output does not
parse into a valid DesignSpec, one repair attempt is made (feeding back the
validation error); after that the prediction is recorded as non-compliant and
will score 0.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from .prompt import build_prompt
from .providers import Completion, CompleteFn, Usage
from .schema import Catalogs, DesignSpec, Scenario

REPAIR_TEMPLATE = (
    "Your previous response was not a valid design spec: {error}\n"
    "Respond again with ONLY the corrected JSON object, nothing else."
)


class PredictionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    instance_id: str
    model: str
    reasoning: str = "none"
    raw_output: str
    spec: DesignSpec | None
    format_compliant: bool
    repair_attempted: bool
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0

    @property
    def is_provider_failure(self) -> bool:
        """Infrastructure failure (timeout, 4xx/5xx) — retryable, never cached."""
        return self.error.startswith("provider error")


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


def run_scenario(
    scenario: Scenario,
    catalogs: Catalogs,
    model_spec: str,
    complete_fn: CompleteFn,
    reasoning: str = "none",
) -> PredictionRecord:
    import time  # noqa: PLC0415

    prompt = build_prompt(scenario, catalogs)
    messages: list[dict] = [{"role": "user", "content": prompt}]
    usage = Usage()
    started = time.monotonic()

    def _record(raw: str, spec, error: str, repaired: bool) -> PredictionRecord:
        return PredictionRecord(
            instance_id=scenario.instance_id,
            model=model_spec,
            reasoning=reasoning,
            raw_output=raw,
            spec=spec,
            format_compliant=spec is not None,
            repair_attempted=repaired,
            error=error,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=round(usage.cost_usd, 6),
            duration_s=round(time.monotonic() - started, 2),
        )

    try:
        completion = complete_fn(messages)
    except Exception as exc:  # provider failure fails the scenario, not the run
        return _record("", None, f"provider error: {exc}", False)
    usage = usage + completion.usage
    spec, error = parse_spec(completion.text)
    if spec is not None:
        return _record(completion.text, spec, "", False)

    messages = messages + [
        {"role": "assistant", "content": completion.text},
        {"role": "user", "content": REPAIR_TEMPLATE.format(error=error)},
    ]
    try:
        retry = complete_fn(messages)
    except Exception as exc:
        return _record(completion.text, None, f"provider error on repair: {exc}", True)
    usage = usage + retry.usage
    spec, error = parse_spec(retry.text)
    return _record(retry.text, spec, error, True)


def run_scenarios(
    scenarios: list[Scenario],
    catalogs: Catalogs,
    model_spec: str,
    complete_fn: CompleteFn,
    reasoning: str = "none",
    skip_instance_ids: set[str] | None = None,
    on_progress: Callable[[PredictionRecord], None] | None = None,
    parallel: int = 1,
) -> list[PredictionRecord]:
    """Run scenarios, optionally with a thread pool.

    Records are returned (and reported via on_progress) in completion order;
    on_progress is always invoked from the calling thread, so file appends
    stay single-threaded.
    """
    skip = skip_instance_ids or set()
    todo = [s for s in scenarios if s.instance_id not in skip]
    records: list[PredictionRecord] = []

    if parallel <= 1:
        for scenario in todo:
            record = run_scenario(scenario, catalogs, model_spec, complete_fn, reasoning)
            records.append(record)
            if on_progress:
                on_progress(record)
        return records

    from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(run_scenario, s, catalogs, model_spec, complete_fn, reasoning): s
            for s in todo
        }
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            if on_progress:
                on_progress(record)
    return records


# ---------------------------------------------------------------------------
# Prediction cache: (model, reasoning, instance_id) -> record.
# Results vary by thinking effort, so effort is part of the key. Provider
# failures (timeouts, 4xx/5xx) are never cached — they are retryable
# infrastructure noise, not model results. Genuine outputs, including
# format-noncompliant ones, are valid benchmark results and are cached.
# ---------------------------------------------------------------------------

def cache_path(cache_dir: Path, model_spec: str, reasoning: str, instance_id: str) -> Path:
    safe_model = model_spec.replace("/", "__")
    return cache_dir / safe_model / reasoning / f"{instance_id}.json"


def load_cached(cache_dir: Path, model_spec: str, reasoning: str, instance_id: str) -> PredictionRecord | None:
    path = cache_path(cache_dir, model_spec, reasoning, instance_id)
    if not path.is_file():
        return None
    try:
        return PredictionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None  # unreadable cache entry counts as a miss


def store_cached(cache_dir: Path, record: PredictionRecord) -> None:
    if record.is_provider_failure:
        return
    path = cache_path(cache_dir, record.model, record.reasoning, record.instance_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(record.model_dump_json(), encoding="utf-8")
    tmp.replace(path)


def append_prediction(record: PredictionRecord, path: Path) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
        f.flush()


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
