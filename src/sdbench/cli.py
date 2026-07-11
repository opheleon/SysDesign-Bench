"""SysDesign-Bench command-line interface.

Decoupled generate/grade flow (SWE-bench convention):
  sdbench run    -> predictions.jsonl   (needs API keys; the only LLM step)
  sdbench grade  -> scores.json         (pure Python, zero LLM calls)
  sdbench report -> markdown table
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .grade import grade_spec
from .prompt import build_prompt
from .providers import build_complete_fn, estimate_cost, parse_model_spec
from .runner import append_prediction, read_predictions, run_scenarios
from .schema import Catalogs, Scenario, load_catalogs, load_scenario, load_scenarios
from .scoring import RunScores, render_report, score
from .validate import validate_catalogs, validate_scenario


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from .env into the environment (existing vars win)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)


def _load(args: argparse.Namespace) -> tuple[Catalogs, list[Scenario]]:
    catalogs = load_catalogs(Path(args.catalogs))
    scenarios = load_scenarios(Path(args.scenarios))
    if not scenarios:
        raise SystemExit(f"no scenario yaml files found under {args.scenarios}")
    return catalogs, scenarios


def _cmd_validate(args: argparse.Namespace) -> int:
    catalog_dir = Path(args.catalogs)
    scenario_dir = Path(args.scenarios)

    try:
        catalogs = load_catalogs(catalog_dir)
    except (OSError, ValueError, ValidationError) as exc:
        print(f"FAIL: could not load catalogs from {catalog_dir}: {exc}")
        return 1

    errors = validate_catalogs(catalogs)

    paths = sorted(scenario_dir.rglob("*.yaml"))
    if not paths:
        print(f"FAIL: no scenario yaml files found under {scenario_dir}")
        return 1

    n_ok = 0
    for path in paths:
        try:
            scenario = load_scenario(path)
        except (OSError, ValueError, ValidationError) as exc:
            errors.append(f"{path}: failed to parse: {exc}")
            continue
        scenario_errors = validate_scenario(scenario, catalogs)
        if scenario_errors:
            errors.extend(scenario_errors)
        else:
            n_ok += 1

    for e in errors:
        print(f"ERROR: {e}")
    print(f"{n_ok}/{len(paths)} scenarios valid, {len(errors)} error(s).")
    return 1 if errors else 0


    # Assumed completion tokens per scenario for pre-run cost estimates; with
    # high reasoning effort, thinking tokens bill as output.
_EST_COMPLETION_TOKENS = {"none": 3000, "low": 5000, "medium": 8000, "high": 12000}


def _cmd_run(args: argparse.Namespace) -> int:
    _load_dotenv()
    catalogs, scenarios = _load(args)
    reasoning = None if args.reasoning == "none" else args.reasoning
    out = Path(args.out)

    skip: set[str] = set()
    if args.resume and out.exists():
        skip = {r.instance_id for r in read_predictions(out)}
        print(f"Resuming: {len(skip)} prediction(s) already in {out}.", flush=True)
    elif out.exists():
        out.unlink()

    # Pre-run cost estimate from real prompt sizes (chars/4 ~= tokens) plus an
    # assumed completion budget per scenario.
    _, model_id = parse_model_spec(args.model)
    todo = [s for s in scenarios if s.instance_id not in skip]
    est_prompt_tokens = sum(len(build_prompt(s, catalogs)) // 4 for s in todo)
    est_completion = _EST_COMPLETION_TOKENS[args.reasoning] * len(todo)
    est = estimate_cost(model_id, est_prompt_tokens, est_completion)
    print(
        f"Running {args.model} on {len(todo)} scenario(s), reasoning={args.reasoning} | "
        f"estimated cost: ~${est:.2f} "
        f"(~{est_prompt_tokens:,} prompt + ~{est_completion:,} completion tokens)",
        flush=True,
    )

    complete_fn = build_complete_fn(
        args.model, reasoning=reasoning, api_base=args.api_base, api_key_env=args.api_key_env
    )

    def on_progress(r) -> None:
        append_prediction(r, out)
        status = "ok" if r.format_compliant else f"FAIL ({r.error[:60]})"
        print(
            f"  {r.instance_id}: {status}"
            f"{' (after repair)' if r.repair_attempted and r.format_compliant else ''}"
            f"  [${r.cost_usd:.4f}]",
            flush=True,
        )

    records = run_scenarios(
        scenarios, catalogs, args.model, complete_fn,
        skip_instance_ids=skip, on_progress=on_progress,
    )
    compliant = sum(r.format_compliant for r in records)
    total_cost = sum(r.cost_usd for r in records)
    total_prompt = sum(r.prompt_tokens for r in records)
    total_completion = sum(r.completion_tokens for r in records)
    print(
        f"Wrote {len(records)} prediction(s) to {out} ({compliant}/{len(records)} format-compliant). "
        f"Actual cost: ${total_cost:.2f} "
        f"({total_prompt:,} prompt + {total_completion:,} completion tokens).",
        flush=True,
    )
    return 0


def _cmd_grade(args: argparse.Namespace) -> int:
    catalogs, scenarios = _load(args)
    del catalogs  # grading needs only the scenarios; catalogs validated at authoring time
    by_id = {s.instance_id: s for s in scenarios}

    records = read_predictions(Path(args.predictions))
    if not records:
        raise SystemExit(f"no predictions found in {args.predictions}")

    all_scores: list[RunScores] = []
    for model in sorted({r.model for r in records}):
        model_records = [r for r in records if r.model == model]
        results = []
        for record in model_records:
            scenario = by_id.get(record.instance_id)
            if scenario is None:
                raise SystemExit(
                    f"prediction references unknown scenario '{record.instance_id}' "
                    f"(not found under {args.scenarios})"
                )
            results.append(grade_spec(scenario, record.spec))
        all_scores.append(score(results, model=model, benchmark_version=__version__))

    out = Path(args.out)
    out.write_text(
        json.dumps([s.model_dump(mode="json") for s in all_scores], indent=2),
        encoding="utf-8",
    )
    for s in all_scores:
        print(
            f"{s.model}: overall {s.overall:.2f} "
            f"(mode accuracy {s.mode_accuracy:.2f}, format compliance {s.format_compliance:.2f})"
        )
    print(f"Wrote {out}.")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    all_scores = []
    for path in args.scores:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        all_scores.extend(RunScores.model_validate(entry) for entry in data)
    print(render_report(all_scores))
    return 0


def _not_implemented(name: str, milestone: str):
    def handler(_args: argparse.Namespace) -> int:
        print(f"'sdbench {name}' is not implemented yet (lands in {milestone}).")
        return 2
    return handler


def _add_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalogs", default="catalogs", help="Catalog directory")
    parser.add_argument("--scenarios", default="scenarios", help="Scenario directory")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdbench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Lint catalogs and scenarios")
    _add_data_args(p_validate)
    p_validate.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="Run a model over scenarios -> predictions.jsonl")
    _add_data_args(p_run)
    p_run.add_argument("--model", required=True,
                       help="provider/model-id: anthropic/claude-fable-5, openai/gpt-5.6-sol, "
                            "xai/grok-4.5, baseten/zai-org/GLM-5.2")
    p_run.add_argument("--out", default="predictions.jsonl")
    p_run.add_argument("--reasoning", choices=["none", "low", "medium", "high"], default="none",
                       help="Reasoning effort: Anthropic extended-thinking budget / OpenAI reasoning_effort")
    p_run.add_argument("--resume", action="store_true",
                       help="Skip instance_ids already present in --out (crash recovery)")
    p_run.add_argument("--api-base", default=None,
                       help="Custom OpenAI-compatible endpoint override")
    p_run.add_argument("--api-key-env", default=None,
                       help="Env var holding the key for --api-base")
    p_run.set_defaults(func=_cmd_run)

    p_grade = sub.add_parser("grade", help="Grade predictions.jsonl -> scores.json (no LLM calls)")
    _add_data_args(p_grade)
    p_grade.add_argument("predictions", help="predictions.jsonl produced by `sdbench run`")
    p_grade.add_argument("--out", default="scores.json")
    p_grade.set_defaults(func=_cmd_grade)

    p_report = sub.add_parser("report", help="Render markdown report from scores.json file(s)")
    p_report.add_argument("scores", nargs="+", help="one or more scores.json files")
    p_report.set_defaults(func=_cmd_report)

    p_build = sub.add_parser("build-dataset")
    p_build.set_defaults(func=_not_implemented("build-dataset", "M3"))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
