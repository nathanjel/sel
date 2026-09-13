#!/usr/bin/env python3
"""Comparable steady-state benchmark wrapper for every SEL lane.

Each in-memory host is launched once, with the fixture and oracle paths passed
explicitly. The host owns evaluator/materialization timers; this wrapper
validates the report contract, aggregates raw samples, and prints the summary.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_support import fixture_metadata, resolve_path, runtime_metadata, sha256_file, stats


IN_MEMORY_LANES = ("lisp", "cpp", "js", "php", "python")
DATABASE_LANES = ("postgresql", "mariadb")
PHASES = ("program_run_ms", "materialize_ms", "prepared_total_ms")
DB_PHASES = ("db_execute_fetch_ms", "db_materialize_ms", "continuation_ms", "hybrid_total_ms")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the corrected SEL lane benchmark")
    parser.add_argument("--mode", choices=("steady-state", "cold-process"), default="steady-state",
                        help="steady-state reuses one host process; cold-process launches one per sample")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--dataset", type=Path, default=ROOT / "tools/scale-test/dataset-10x.json")
    parser.add_argument("--reference", type=Path, default=ROOT / "tools/scale-test/benchmark_results.json")
    parser.add_argument("--timing-mode", choices=("steady-state", "gc-controlled"), default="steady-state")
    parser.add_argument("--only", default=None, help="comma-separated scenario ids")
    parser.add_argument("--scale", type=int, choices=(10, 100), default=None,
                        help="database scale selector; validates the selected fixture/database")
    parser.add_argument("--database", default=None,
                        help="explicit database name, otherwise derived from --scale/fixture")
    parser.add_argument("--output", type=Path, default=ROOT / "tools/scale-test/benchmark_results_corrected.json")
    parser.add_argument("--skip-db", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def load_reference(path: Path) -> list[dict[str, Any]]:
    value = load_json(path)
    if not isinstance(value, list) or len(value) != 6:
        raise RuntimeError(f"expected exactly six scenarios in {path}")
    ids = [str(item.get("id")) for item in value]
    if len(ids) != len(set(ids)):
        raise RuntimeError("reference contains duplicate scenario ids")
    return value


def run_process(command: list[str], *, label: str, cwd: Path = ROOT,
                env: dict[str, str] | None = None, stream: bool = True) -> None:
    print(f"[start] {label}: {' '.join(command)}", flush=True)
    if not stream:
        process = subprocess.run(command, cwd=cwd, env=env, text=True,
                                 capture_output=True)
        if process.returncode != 0:
            raise RuntimeError(
                f"{label} failed ({process.returncode})\n{process.stdout}{process.stderr}"
            )
        return
    process = subprocess.Popen(command, cwd=cwd, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               bufsize=1)
    assert process.stdout is not None
    try:
        for line in process.stdout:
            print(line.rstrip(), flush=True)
        returncode = process.wait()
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    if returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {returncode}")


def expected_ids(reference: list[dict[str, Any]]) -> list[str]:
    return [str(item["id"]) for item in reference]


def validate_report(report: dict[str, Any], lane: str,
                    reference: list[dict[str, Any]], fixture: dict[str, Any],
                    runs: int, warmups: int, timing_mode: str,
                    database: str | None = None,
                    reference_path: Path | None = None) -> None:
    if report.get("schema_version") != 2:
        raise RuntimeError(f"{lane} report has unsupported schema_version")
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list):
        raise RuntimeError(f"{lane} report has no scenario list")
    ids = [str(item.get("id")) for item in scenarios]
    if ids != expected_ids(reference):
        raise RuntimeError(f"{lane} scenario order/ids differ: {ids}")
    metadata = report.get("metadata", {})
    if metadata.get("runs") != runs or metadata.get("warmups") != warmups:
        raise RuntimeError(f"{lane} report has wrong run configuration")
    if metadata.get("timing_mode") != timing_mode:
        raise RuntimeError(f"{lane} report has wrong timing mode")
    if lane == "php" and metadata.get("runtime", {}).get("jit_available") is not True:
        raise RuntimeError("PHP JIT-enabled benchmark did not report an active JIT")
    if lane == "php":
        context_prepare_ms = metadata.get("context_prepare_ms")
        if not isinstance(context_prepare_ms, (int, float)) or not math.isfinite(context_prepare_ms) or context_prepare_ms < 0:
            raise RuntimeError("PHP report has no valid untimed context preparation phase")
        if not isinstance(metadata.get("shape_interning"), dict):
            raise RuntimeError("PHP report has no shape-cache instrumentation")
        if not isinstance(metadata.get("memory_after_context"), dict):
            raise RuntimeError("PHP report has no post-ingestion memory snapshot")
    if lane in DATABASE_LANES and database is not None and metadata.get("database") != database:
        raise RuntimeError(f"{lane} report used database {metadata.get('database')!r}, expected {database!r}")
    if reference_path is not None:
        expected_reference_path = reference_path.resolve()
        if Path(metadata.get("reference_path", "")).resolve() != expected_reference_path:
            raise RuntimeError(f"{lane} report used a different reference path")
        if metadata.get("reference_sha256") != sha256_file(expected_reference_path):
            raise RuntimeError(f"{lane} report used a different reference SHA-256")
        if metadata.get("reference_scenario_ids") != expected_ids(reference):
            raise RuntimeError(f"{lane} report has different reference scenario IDs")
    child_fixture = metadata.get("fixture", {})
    if child_fixture.get("path") is None:
        raise RuntimeError(f"{lane} report has no fixture identity")
    if Path(child_fixture["path"]).resolve() != Path(fixture["path"]).resolve():
        raise RuntimeError(f"{lane} report used a different fixture path")
    if child_fixture.get("sha256") != fixture["sha256"]:
        raise RuntimeError(f"{lane} report used a different fixture SHA-256")
    if child_fixture.get("table_rows") != fixture["table_rows"]:
        raise RuntimeError(f"{lane} report used different fixture table counts")
    if child_fixture.get("total_source_rows") != fixture["total_source_rows"]:
        raise RuntimeError(f"{lane} report used a different source-row count")
    if lane in IN_MEMORY_LANES:
        representation = metadata.get("representation", {})
        if representation.get("shaped_records") != fixture["total_source_rows"]:
            raise RuntimeError(f"{lane} did not shape every fixture record")
        # The prepared root context is the one intentional fallback record;
        # any additional fallback means the homogeneous fixture was loaded
        # through a generic property/hash representation.
        if representation.get("fallback_records") != 1:
            raise RuntimeError(f"{lane} has unexpected fallback record count: {representation}")
        if representation.get("lists") != len(fixture["table_rows"]):
            raise RuntimeError(f"{lane} has unexpected list representation count: {representation}")
    if lane in DATABASE_LANES:
        if metadata.get("table_rows") != fixture["table_rows"]:
            raise RuntimeError(f"{lane} has unexpected database table counts: {metadata.get('table_rows')!r}")
        if metadata.get("total_source_rows") != fixture["total_source_rows"]:
            raise RuntimeError(f"{lane} has unexpected database source-row count")
    for item in scenarios:
        if not item.get("passed", False) or report.get("passed") is False:
            raise RuntimeError(f"{lane} parity failed for {item.get('id')}: {item.get('failures')}")
        if lane in IN_MEMORY_LANES and item.get("context_unchanged") is not True:
            raise RuntimeError(f"{lane}/{item.get('id')} did not prove context immutability")
        if item.get("parity", {}).get("passed") is not True:
            raise RuntimeError(f"{lane}/{item.get('id')} did not pass its parity gate")
        samples = item.get("samples")
        if not isinstance(samples, list) or len(samples) != runs:
            raise RuntimeError(f"{lane}/{item.get('id')} expected {runs} measured samples")
        for sample in samples:
            phases = DB_PHASES if lane in ("postgresql", "mariadb") else PHASES
            for phase in phases:
                value = sample.get(phase)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise RuntimeError(f"{lane}/{item.get('id')} has invalid {phase}: {value!r}")
            if lane == "php":
                for phase in ("scenario_prepare_ms", "end_to_end_ms"):
                    value = sample.get(phase)
                    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                        raise RuntimeError(f"php/{item.get('id')} has invalid {phase}: {value!r}")
                if not isinstance(sample.get("memory"), dict):
                    raise RuntimeError(f"php/{item.get('id')} has no sample memory snapshot")


def run_lisp(dataset: Path, reference: Path, report_path: Path,
             runs: int, warmups: int, timing_mode: str,
             only: str | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sel-scale-lisp-") as temporary:
        worktree = Path(temporary)
        scale_dir = worktree / "tools/scale-test"
        scale_dir.mkdir(parents=True)
        (worktree / "lisp").symlink_to(ROOT / "lisp", target_is_directory=True)
        (scale_dir / "sel_benchmarks.lisp").symlink_to(ROOT / "tools/scale-test/sel_benchmarks.lisp")
        environment = os.environ.copy()
        environment.update({
            "SEL_BENCHMARK_MODE": "steady-state",
            "SEL_BENCHMARK_OUTPUT": str(report_path),
            "SEL_BENCHMARK_REFERENCE": str(reference),
            "SEL_BENCHMARK_RUNS": str(runs),
            "SEL_BENCHMARK_WARMUPS": str(warmups),
            "SEL_BENCHMARK_TIMING_MODE": timing_mode,
            "SEL_DATASET_FILE": str(dataset),
        })
        if only:
            environment["SEL_BENCHMARK_ONLY"] = only
        run_process([
            "sbcl", "--dynamic-space-size", "4096", "--noinform",
            "--disable-debugger", "--non-interactive",
            "--load", "tools/scale-test/sel_benchmarks.lisp",
        ], label="lisp", cwd=worktree, env=environment)
    return load_json(report_path)


def run_cpp(dataset: Path, reference: Path, report_path: Path,
            runs: int, warmups: int, timing_mode: str,
            only: str | None = None, build: bool = True) -> dict[str, Any]:
    if build:
        run_process(["make", "-B", "-C", "cpp", "-j2", "build/scale-bench"],
                    label="cpp build", stream=False)
    command = [
        str(ROOT / "cpp/build/scale-bench"),
        "--dataset", str(dataset), "--reference", str(reference),
        "--output", str(report_path), "--runs", str(runs),
        "--warmups", str(warmups), "--timing-mode", timing_mode,
    ]
    if only:
        command.extend(["--only", only])
    run_process(command, label="cpp")
    report = load_json(report_path)
    report.setdefault("metadata", {})["build"] = {
        "build_command": "make -B -C cpp -j2 build/scale-bench",
        "built_from_current_sources": True,
        "binary_path": str((ROOT / "cpp/build/scale-bench").resolve()),
        "binary_sha256": sha256_file(ROOT / "cpp/build/scale-bench"),
    }
    return report


def run_json_lane(lane: str, command: list[str], dataset: Path, reference: Path,
                  report_path: Path, runs: int, warmups: int,
                  timing_mode: str, env: dict[str, str] | None = None,
                  only: str | None = None) -> dict[str, Any]:
    full_command = command + ["--dataset", str(dataset), "--reference", str(reference),
                           "--output", str(report_path), "--runs", str(runs),
                           "--warmups", str(warmups), "--timing-mode", timing_mode]
    if only:
        full_command.extend(["--only", only])
    run_process(full_command,
                label=lane, env=env)
    return load_json(report_path)


def run_cold_in_memory_once(lane: str, dataset: Path, reference: Path,
                            report_path: Path, scenario_id: str,
                            timing_mode: str) -> tuple[dict[str, Any], float]:
    """Launch exactly one host process for one scenario/sample."""
    started = time.perf_counter()
    if lane == "lisp":
        report = run_lisp(dataset, reference, report_path, 1, 0, timing_mode, scenario_id)
    elif lane == "cpp":
        report = run_cpp(dataset, reference, report_path, 1, 0, timing_mode, scenario_id, build=False)
    elif lane == "js":
        command = ["node"]
        if timing_mode == "gc-controlled":
            command.append("--expose-gc")
        command.append(str(ROOT / "tools/scale-test/sel_benchmarks.mjs"))
        report = run_json_lane("cold-js", command, dataset, reference, report_path,
                               1, 0, timing_mode, only=scenario_id)
    elif lane == "php":
        report = run_json_lane(
            "cold-php",
            ["php", "-d", "memory_limit=-1", "-d", "opcache.enable_cli=1",
             "-d", "opcache.jit_buffer_size=128M", "-d", "opcache.jit=1255",
             str(ROOT / "tools/scale-test/sel_benchmarks.php")],
            dataset, reference, report_path, 1, 0, timing_mode, only=scenario_id)
    elif lane == "python":
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "python")
        report = run_json_lane(
            "cold-python", [sys.executable, str(ROOT / "tools/scale-test/sel_benchmarks.py")],
            dataset, reference, report_path, 1, 0, timing_mode, environment, scenario_id)
    else:
        raise RuntimeError(f"unsupported cold in-memory lane: {lane}")
    return report, (time.perf_counter() - started) * 1000.0


def run_cold_database_once(dialect: str, dataset: Path, reference: Path,
                           report_path: Path, scenario_id: str,
                           timing_mode: str, database: str) -> tuple[dict[str, Any], float]:
    """Launch one database benchmark process for one dialect/scenario/sample."""
    command = [
        sys.executable, str(ROOT / "tools/scale-test/run_benchmarks.py"),
        "--dataset", str(dataset), "--reference", str(reference),
        "--output", str(report_path), "--runs", "1", "--warmups", "0",
        "--timing-mode", timing_mode, "--only", scenario_id,
        "--database", database, "--dialect", dialect,
    ]
    started = time.perf_counter()
    run_process(command, label=f"cold-{dialect}")
    artifact = load_json(report_path)
    return artifact["lanes"][dialect], (time.perf_counter() - started) * 1000.0


def run_cold_process_benchmark(dataset: Path, reference_path: Path,
                               reference: list[dict[str, Any]], fixture: dict[str, Any],
                               runs: int, timing_mode: str, database: str | None,
                               skip_db: bool) -> dict[str, Any]:
    """Build a separately labelled cold-process report.

    Each in-memory lane/scenario/sample is a fresh host process.  Database
    cold samples likewise use a fresh Python/PDO process per dialect/scenario.
    The C++ binary is built once before the cold measurements and is not charged
    to the process timings.
    """
    if runs < 1:
        raise ValueError("runs must be positive")
    report: dict[str, Any] = {
        "schema_version": 2,
        "metadata": {
            "mode": "cold-process",
            "fixture": fixture,
            "reference_path": str(reference_path.resolve()),
            "reference_sha256": sha256_file(reference_path),
            "reference_scenario_ids": expected_ids(reference),
            "runtime": runtime_metadata(),
            "runs": runs,
            "warmups": 0,
            "timing_mode": timing_mode,
            "scenario_order": expected_ids(reference),
            "database": database,
            "lanes": [],
        },
        "lanes": {},
        "passed": True,
    }
    with tempfile.TemporaryDirectory(prefix="sel-cold-reports-") as temporary:
        report_dir = Path(temporary)
        run_process(["make", "-B", "-C", "cpp", "-j2", "build/scale-bench"],
                    label="cold cpp build", stream=False)
        for lane in IN_MEMORY_LANES:
            lane_samples: dict[str, list[dict[str, float]]] = {item["id"]: [] for item in reference}
            lane_metadata: dict[str, Any] = {}
            for expected in reference:
                scenario_id = expected["id"]
                for run in range(runs):
                    print(f"[cold/{lane}] {scenario_id} process {run + 1}/{runs}", flush=True)
                    child, cold_ms = run_cold_in_memory_once(
                        lane, dataset, reference_path,
                        report_dir / f"{lane}-{scenario_id}-{run}.json",
                        scenario_id, timing_mode)
                    validate_report(
                        child, lane, [expected], fixture, 1, 0, timing_mode,
                        reference_path=reference_path,
                    )
                    lane_metadata = child.get("metadata", {})
                    sample = dict(child["scenarios"][0]["samples"][0])
                    sample["cold_process_ms"] = cold_ms
                    lane_samples[scenario_id].append(sample)
            lane_output = {
                "implementation": lane,
                "metadata": {"mode": "cold-process", "child": lane_metadata},
                "scenarios": {},
            }
            for expected in reference:
                scenario_id = expected["id"]
                samples = lane_samples[scenario_id]
                lane_output["scenarios"][scenario_id] = {
                    "rows": len(expected.get("in_memory_rows", [])),
                    "parity": {"passed": True, "failures": []},
                    "statistics": {
                        phase: stats(sample[phase] for sample in samples)
                        for phase in ("cold_process_ms",) + PHASES
                    },
                    "raw_samples": samples,
                }
            report["lanes"][lane] = lane_output
            report["metadata"]["lanes"].append(lane)

        if not skip_db:
            if database is None:
                raise RuntimeError("cold database benchmark requires a selected database")
            for dialect in DATABASE_LANES:
                lane_samples = {item["id"]: [] for item in reference}
                lane_metadata: dict[str, Any] = {}
                for expected in reference:
                    scenario_id = expected["id"]
                    for run in range(runs):
                        print(f"[cold/{dialect}] {scenario_id} process {run + 1}/{runs}", flush=True)
                        child, cold_ms = run_cold_database_once(
                            dialect, dataset, reference_path,
                            report_dir / f"{dialect}-{scenario_id}-{run}.json",
                            scenario_id, timing_mode, database)
                        validate_report(
                            child, dialect, [expected], fixture, 1, 0,
                            timing_mode, database, reference_path,
                        )
                        lane_metadata = child.get("metadata", {})
                        sample = dict(child["scenarios"][0]["samples"][0])
                        sample["cold_process_ms"] = cold_ms
                        lane_samples[scenario_id].append(sample)
                lane_output = {
                    "implementation": dialect,
                    "metadata": {"mode": "cold-process", "child": lane_metadata},
                    "scenarios": {},
                }
                for expected in reference:
                    scenario_id = expected["id"]
                    samples = lane_samples[scenario_id]
                    lane_output["scenarios"][scenario_id] = {
                        "rows": len(expected.get("in_memory_rows", [])),
                        "parity": {"passed": True, "failures": []},
                        "statistics": {
                            phase: stats(sample[phase] for sample in samples)
                            for phase in ("cold_process_ms",) + DB_PHASES
                        },
                        "raw_samples": samples,
                    }
                report["lanes"][dialect] = lane_output
                report["metadata"]["lanes"].append(dialect)
    return report


def print_cold_report(report: dict[str, Any], reference: list[dict[str, Any]]) -> None:
    lanes = list(report["lanes"])
    print()
    print("SEL cold-process benchmark — cold_process_ms")
    print("Each sample is a fresh lane process; build/setup policy is in metadata.")
    print(" | ".join(["Scenario"] + [lane.upper() for lane in lanes]))
    print("-" * (18 + 26 * len(lanes)))
    for expected in reference:
        cells = [expected["id"]]
        for lane in lanes:
            value = report["lanes"][lane]["scenarios"][expected["id"]]["statistics"]["cold_process_ms"]
            cells.append(format_stats(value))
        print(" | ".join(cells))


def read_report(path: Path, lane: str, reference: list[dict[str, Any]],
                fixture: dict[str, Any], runs: int, warmups: int,
                timing_mode: str) -> dict[str, Any]:
    report = load_json(path)
    if not isinstance(report, dict):
        raise RuntimeError(f"{lane} did not return a JSON object")
    validate_report(report, lane, reference, fixture, runs, warmups, timing_mode)
    return report


def aggregate_reports(reports: dict[str, dict[str, Any]], reference: list[dict[str, Any]],
                      fixture: dict[str, Any], reference_path: Path,
                      runs: int, warmups: int, timing_mode: str,
                      database: str | None = None) -> dict[str, Any]:
    aggregate: dict[str, Any] = {
        "schema_version": 2,
        "metadata": {
            "mode": "steady-state",
            "fixture": fixture,
            "reference_path": str(reference_path.resolve()),
            "reference_sha256": sha256_file(reference_path),
            "reference_scenario_ids": expected_ids(reference),
            "runtime": runtime_metadata(),
            "runs": runs,
            "warmups": warmups,
            "timing_mode": timing_mode,
            "scenario_order": expected_ids(reference),
            "lanes": list(reports),
            "database": database,
        },
        "lanes": {},
        "passed": True,
    }
    for lane, report in reports.items():
        lane_output = {
            "implementation": report.get("implementation", lane),
            # Keep the host-owned runtime, fixture, representation, and
            # timing metadata in the aggregate artifact; validation above
            # must not make this evidence disappear from the final report.
            "metadata": report.get("metadata", {}),
            "scenarios": {},
        }
        for scenario in report["scenarios"]:
            phase_output = {}
            phases = list(DB_PHASES if lane in ("postgresql", "mariadb") else PHASES)
            if lane not in DATABASE_LANES and all(
                phase in sample
                for phase in ("scenario_prepare_ms", "end_to_end_ms")
                for sample in scenario["samples"]
            ):
                phases.extend(("scenario_prepare_ms", "end_to_end_ms"))
            for phase in phases:
                phase_output[phase] = stats(float(sample[phase]) for sample in scenario["samples"])
            lane_output["scenarios"][scenario["id"]] = {
                "rows": scenario.get("rows"),
                "compile_ms": scenario.get("compile_ms"),
                "context_unchanged": scenario.get("context_unchanged", True),
                "parity": scenario.get("parity", {"passed": scenario.get("passed", False)}),
                "statistics": phase_output,
                "raw_samples": scenario["samples"],
            }
        aggregate["lanes"][lane] = lane_output
        aggregate["passed"] = aggregate["passed"] and bool(report.get("passed", False))
    return aggregate


def format_stats(value: dict[str, Any]) -> str:
    return (f"mean {value['mean_ms']:.2f}; median {value['median_ms']:.2f}; "
            f"range {value['min_ms']:.2f}-{value['max_ms']:.2f}; CV {value['cv']:.3f}")


def print_report(aggregate: dict[str, Any], reference: list[dict[str, Any]]) -> None:
    lanes = list(aggregate["lanes"])
    memory_lanes = [lane for lane in lanes if lane in IN_MEMORY_LANES]
    fixture = aggregate["metadata"]["fixture"]
    print()
    print("SEL steady-state benchmark — program_run_ms")
    print(f"fixture rows: {fixture['total_source_rows']}; runs={aggregate['metadata']['runs']}; "
          f"warmups={aggregate['metadata']['warmups']}; raw samples retained")
    print("Each cell: mean / median ms, min-max, sample CV; p95 is n/a below 20 samples.")
    print(" | ".join(["Scenario"] + [lane.upper() for lane in memory_lanes]))
    print("-" * (18 + 26 * len(memory_lanes)))
    for item in reference:
        cells = [item["id"]]
        for lane in memory_lanes:
            value = aggregate["lanes"][lane]["scenarios"][item["id"]]["statistics"]["program_run_ms"]
            cells.append(format_stats(value))
        print(" | ".join(cells))
    print("\nParity: " + ", ".join(f"{lane.upper()} PASS" for lane in lanes))
    db_lanes = [lane for lane in lanes if lane in DATABASE_LANES]
    if db_lanes:
        print("\nSEL database client benchmark — hybrid_total_ms (persistent PDO, execute/fetch boundary)")
        print(" | ".join(["Scenario"] + [lane.upper() for lane in db_lanes]))
        print("-" * (18 + 26 * len(db_lanes)))
        for item in reference:
            cells = [item["id"]]
            for lane in db_lanes:
                value = aggregate["lanes"][lane]["scenarios"][item["id"]]["statistics"]["hybrid_total_ms"]
                cells.append(format_stats(value))
            print(" | ".join(cells))


def main() -> int:
    args = parse_args()
    if args.runs < 1 or args.warmups < 0:
        raise SystemExit("--runs must be positive and --warmups must be non-negative")
    dataset = resolve_path(args.dataset)
    reference_path = resolve_path(args.reference)
    output = resolve_path(args.output)
    if not dataset.is_file() or not reference_path.is_file():
        raise SystemExit("dataset and reference must exist")
    reference = load_reference(reference_path)
    if args.only:
        wanted = set(args.only.split(","))
        unknown = wanted.difference(expected_ids(reference))
        if unknown:
            raise SystemExit(f"unknown scenario ids: {sorted(unknown)}")
        reference = [item for item in reference if item["id"] in wanted]
        if not reference:
            raise SystemExit("--only selected no scenarios")
    dataset_value = load_json(dataset)
    fixture = fixture_metadata(dataset, dataset_value)
    if args.scale is not None:
        expected_scale = 100 if args.scale == 100 else 10
        fixture_scale = 100 if fixture["total_source_rows"] > 500_000 else 10
        if fixture_scale != expected_scale:
            raise SystemExit(
                f"--scale {args.scale} does not match fixture source rows "
                f"({fixture['total_source_rows']})"
            )
    database = args.database or os.environ.get("SEL_BENCH_DATABASE")
    if database is None and not args.skip_db:
        database = "sel_oracle_100x" if fixture["total_source_rows"] > 500_000 else "sel_oracle"
    if args.mode == "cold-process":
        cold_report = run_cold_process_benchmark(
            dataset, reference_path, reference, fixture, args.runs,
            args.timing_mode, database, args.skip_db,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(cold_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print_cold_report(cold_report, reference)
        print(f"Machine-readable report: {output}")
        return 0 if cold_report["passed"] else 1
    reports: dict[str, dict[str, Any]] = {}
    started = time.monotonic()
    print(f"Starting corrected steady-state benchmark: {fixture['total_source_rows']} source rows; "
          f"{args.runs} measured + {args.warmups} warmups per scenario.", flush=True)
    with tempfile.TemporaryDirectory(prefix="sel-scale-reports-") as temporary:
        report_dir = Path(temporary)
        reports["lisp"] = run_lisp(dataset, reference_path, report_dir / "lisp.json",
                                    args.runs, args.warmups, args.timing_mode, args.only)
        reports["cpp"] = run_cpp(dataset, reference_path, report_dir / "cpp.json",
                                   args.runs, args.warmups, args.timing_mode, args.only)
        js_command = ["node"]
        if args.timing_mode == "gc-controlled":
            js_command.append("--expose-gc")
        js_command.append(str(ROOT / "tools/scale-test/sel_benchmarks.mjs"))
        reports["js"] = run_json_lane(
            "js", js_command,
            dataset, reference_path, report_dir / "js.json", args.runs,
            args.warmups, args.timing_mode, only=args.only)
        reports["php"] = run_json_lane(
            "php", ["php", "-d", "memory_limit=-1", "-d", "opcache.enable_cli=1",
                    "-d", "opcache.jit_buffer_size=128M", "-d", "opcache.jit=1255",
                    str(ROOT / "tools/scale-test/sel_benchmarks.php")],
            dataset, reference_path, report_dir / "php.json", args.runs,
            args.warmups, args.timing_mode, only=args.only)
        python_environment = os.environ.copy()
        python_environment["PYTHONPATH"] = str(ROOT / "python")
        reports["python"] = run_json_lane(
            "python", [sys.executable, str(ROOT / "tools/scale-test/sel_benchmarks.py")],
            dataset, reference_path, report_dir / "python.json", args.runs,
            args.warmups, args.timing_mode, python_environment, args.only)
        if not args.skip_db:
            from run_benchmarks import run_corrected_database_benchmark
            reports.update(run_corrected_database_benchmark(
                dataset, reference_path, args.runs, args.warmups, args.timing_mode,
                reference=reference, database=database))
        for lane, report in reports.items():
            validate_report(
                report, lane, reference, fixture,
                args.runs, args.warmups, args.timing_mode, database,
                reference_path,
            )
    aggregate = aggregate_reports(reports, reference, fixture, reference_path,
                                  args.runs, args.warmups, args.timing_mode, database)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_report(aggregate, reference)
    print(f"Machine-readable report: {output}")
    print(f"Total elapsed: {time.monotonic() - started:.1f}s")
    return 0 if aggregate["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        raise SystemExit(1)
