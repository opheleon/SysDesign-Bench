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
from .runner import make_litellm_complete, read_predictions, run_scenarios, write_predictions
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


def _cmd_run(args: argparse.Namespace) -> int:
    _load_dotenv()
    catalogs, scenarios = _load(args)
    print(f"Running {args.model} on {len(scenarios)} scenario(s), temperature={args.temperature} ...")
    records = run_scenarios(
        scenarios,
        catalogs,
        model=args.model,
        complete_fn=make_litellm_complete(api_base=args.api_base, api_key_env=args.api_key_env),
        temperature=args.temperature,
        on_progress=lambda r: print(
            f"  {r.instance_id}: {'ok' if r.format_compliant else 'FORMAT FAIL'}"
            f"{' (after repair)' if r.repair_attempted and r.format_compliant else ''}"
        ),
    )
    out = Path(args.out)
    write_predictions(records, out)
    compliant = sum(r.format_compliant for r in records)
    print(f"Wrote {len(records)} prediction(s) to {out} ({compliant}/{len(records)} format-compliant).")
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
    p_run.add_argument("--model", required=True, help="litellm model id, e.g. anthropic/claude-opus-4-8")
    p_run.add_argument("--out", default="predictions.jsonl")
    p_run.add_argument("--temperature", type=float, default=0.0)
    p_run.add_argument("--api-base", default=None,
                       help="OpenAI-compatible endpoint override (e.g. https://inference.baseten.co/v1)")
    p_run.add_argument("--api-key-env", default=None,
                       help="Env var holding the key for --api-base (e.g. BASETEN_API_KEY)")
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
