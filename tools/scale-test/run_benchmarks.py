#!/usr/bin/env python3
"""Run the corrected persistent-client PostgreSQL/MariaDB benchmark.

The in-memory hosts are orchestrated by ``benchmark_all.py``.  This module is
also importable by that wrapper and owns only the database lanes.  It keeps one
PHP/PDO client alive per dialect, executes the generated SQL directly, and
runs the generated SEL continuation for hybrid plans.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "python"))

from benchmark_support import fixture_metadata, resolve_path, runtime_metadata, sha256_file, stats


DB_FIELD_TOLERANCES = {
    # PostgreSQL's point-distance and MariaDB's ST_Distance kernels round the
    # same decimal at the final boundary differently on some rows.  This is
    # intentionally the only field-specific tolerance in the DB parity check.
    "dist_berlin": Decimal("0.000001"),
}
_DECIMAL_TEXT = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")


class PersistentPdoClient:
    """One PHP/PDO connection driven by a newline-delimited JSON protocol."""

    def __init__(self, dialect: str, database: str) -> None:
        self.dialect = dialect
        self.process = subprocess.Popen(
            [
                "php",
                str(Path(__file__).resolve().with_name("db_client.php")),
                "--dialect",
                dialect,
                "--database",
                database,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            assert self.process.stdin is not None
            assert self.process.stdout is not None
            line = self.process.stdout.readline()
            if not line:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise RuntimeError(f"{dialect} persistent client did not start: {stderr}")
            ready = json.loads(line)
            if ready.get("ready") is not True:
                raise RuntimeError(f"{dialect} persistent client rejected connection: {ready}")
            self.connect_ms = float(ready.get("connect_ms", 0.0))
            self.boundary = str(ready.get("boundary", "execute/fetch combined"))
            self.prepared_statement_policy = str(ready.get("prepared_statement_policy", "unknown"))
            self.table_rows = ready.get("table_rows")
            self.total_source_rows = ready.get("total_source_rows")
            self._closed = False
        except Exception:
            self.process.kill()
            self.process.wait()
            raise

    def query(self, scenario_id: str, sql: str) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError(f"{self.dialect} persistent client is closed")
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps({"id": scenario_id, "sql": sql}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"{self.dialect} persistent query client stopped: {stderr}")
        response = json.loads(line)
        if response.get("error"):
            raise RuntimeError(f"{self.dialect} query failed: {response['error']}\nSQL:\n{sql}")
        if response.get("id") != scenario_id:
            raise RuntimeError(f"{self.dialect} response id mismatch")
        if not isinstance(response.get("rows"), list):
            raise RuntimeError(f"{self.dialect} response rows are not a list")
        return response

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.stdin:
            self.process.stdin.close()
        returncode = self.process.wait(timeout=30)
        if returncode != 0:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"{self.dialect} persistent client exited {returncode}: {stderr}")


def exact_db_value(value: object) -> object:
    """Normalize transport strings without rounding ordinary numeric fields."""
    if value is None or value == "NULL":
        return None
    text = str(value).strip()
    if _DECIMAL_TEXT.fullmatch(text):
        return ("decimal", Decimal(text))
    return ("text", text)


def compare_rows_exact(
    rows_a: list[dict[str, object]],
    rows_b: list[dict[str, object]],
    label_a: str,
    label_b: str,
) -> tuple[bool, str]:
    """Compare ordered rows exactly, with one explicitly recorded tolerance."""
    if len(rows_a) != len(rows_b):
        return False, f"row count mismatch: {label_a}={len(rows_a)} {label_b}={len(rows_b)}"
    for index, (left, right) in enumerate(zip(rows_a, rows_b)):
        left_keys = {str(key).lower(): key for key in left}
        right_keys = {str(key).lower(): key for key in right}
        if set(left_keys) != set(right_keys):
            return False, f"row {index + 1} key mismatch: {label_a} vs {label_b}"
        for key in sorted(left_keys):
            actual = exact_db_value(left[left_keys[key]])
            expected = exact_db_value(right[right_keys[key]])
            tolerant = (
                key in DB_FIELD_TOLERANCES
                and isinstance(actual, tuple)
                and isinstance(expected, tuple)
                and actual[0] == expected[0] == "decimal"
                and abs(actual[1] - expected[1]) <= DB_FIELD_TOLERANCES[key]
            )
            if actual != expected and not tolerant:
                return (
                    False,
                    f"row {index + 1} field {key}: "
                    f"{label_a}={actual!r} {label_b}={expected!r}",
                )
    return True, "PARITY"


def continuation_rows(plan: Any, raw_rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], float, float]:
    """Materialize and execute the generated Python SEL continuation."""
    from sel import Value
    from sel_benchmarks import benchmark_value

    if plan.pure_sql:
        materialize_start = time.perf_counter()
        canonical_rows = [dict(row) for row in raw_rows]
        materialize_ms = (time.perf_counter() - materialize_start) * 1000.0
        return canonical_rows, materialize_ms, 0.0

    materialize_start = time.perf_counter()
    input_value = Value.from_native(raw_rows)
    materialize_ms = (time.perf_counter() - materialize_start) * 1000.0

    continuation_start = time.perf_counter()
    root = Value.none()
    root.set(plan.continuation_source_var, input_value)
    final_value = plan.continuation_program.run(root)
    final_rows = benchmark_value(final_value)
    continuation_ms = (time.perf_counter() - continuation_start) * 1000.0
    if not isinstance(final_rows, list):
        raise RuntimeError("generated database continuation did not return a row list")
    return final_rows, materialize_ms, continuation_ms


def database_plans(reference: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Compile Python plans and prove SQL/continuation shape against Lisp."""
    from sel import compile
    from sel.sql import Sql
    from sel_benchmarks import register_benchmark_builtins, schema

    register_benchmark_builtins()
    plans: dict[str, dict[str, Any]] = {}
    for expected in reference:
        program = compile(expected["query"])
        dialect_plans: dict[str, Any] = {}
        for dialect in ("postgresql", "mariadb"):
            plan = Sql.plan_hybrid(program, dialect, schema(dialect))
            if plan.sql_statement is None:
                raise RuntimeError(f"{dialect} has no SQL statement for {expected['id']}")
            sql = plan.sql_statement.as_statement("inline")
            expected_sql = expected["sql_postgres" if dialect == "postgresql" else "sql_mariadb"]
            if sql != expected_sql:
                raise RuntimeError(f"database {dialect} SQL differs from Lisp reference for {expected['id']}")
            expected_hybrid = expected.get("is_hybrid") is True or expected.get("has_continuation") is True
            if plan.is_hybrid != expected_hybrid or plan.pure_sql != (not expected_hybrid):
                raise RuntimeError(f"database {dialect} hybrid metadata differs for {expected['id']}")
            if (plan.continuation_program is not None) != (expected.get("has_continuation") is True):
                raise RuntimeError(f"database {dialect} continuation metadata differs for {expected['id']}")
            dialect_plans[dialect] = plan
        plans[expected["id"]] = dialect_plans
    return plans


def run_corrected_database_benchmark(
    dataset: Path,
    reference_path: Path,
    runs: int,
    warmups: int,
    timing_mode: str,
    reference: list[dict[str, Any]] | None = None,
    database: str | None = None,
    dialects: tuple[str, ...] = ("postgresql", "mariadb"),
) -> dict[str, dict[str, Any]]:
    """Run both DB lanes with persistent connections and phase-separated data."""
    if reference is None:
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if runs < 1 or warmups < 0:
        raise ValueError("runs must be positive and warmups must be non-negative")
    if timing_mode not in ("steady-state", "gc-controlled"):
        raise ValueError(f"unsupported timing mode: {timing_mode}")
    if not dialects or any(dialect not in ("postgresql", "mariadb") for dialect in dialects):
        raise ValueError(f"unsupported database dialect set: {dialects}")

    dataset_value = json.loads(dataset.read_text(encoding="utf-8"), parse_float=str)
    fixture = fixture_metadata(dataset, dataset_value)
    plans = database_plans(reference)
    db_name = database or os.environ.get(
        "SEL_BENCH_DATABASE",
        "sel_oracle_100x" if fixture["total_source_rows"] > 500_000 else "sel_oracle",
    )
    clients: dict[str, PersistentPdoClient] = {}
    reports: dict[str, dict[str, Any]] = {}
    raw_outputs: dict[str, dict[str, list[list[dict[str, object]]]]] = {
        dialect: {item["id"]: [] for item in reference}
        for dialect in dialects
    }
    scenario_reports: dict[str, dict[str, dict[str, Any]]] = {
        dialect: {} for dialect in dialects
    }

    try:
        for dialect in dialects:
            clients[dialect] = PersistentPdoClient(dialect, db_name)
        for dialect, client in clients.items():
            if client.table_rows != fixture["table_rows"]:
                raise RuntimeError(
                    f"{dialect} database table counts differ from fixture: "
                    f"{client.table_rows!r} != {fixture['table_rows']!r}"
                )
            if client.total_source_rows != fixture["total_source_rows"]:
                raise RuntimeError(
                    f"{dialect} database source-row count differs from fixture: "
                    f"{client.total_source_rows!r} != {fixture['total_source_rows']}"
                )

        for dialect in dialects:
            client = clients[dialect]
            report: dict[str, Any] = {
                "schema_version": 2,
                "implementation": dialect,
                "passed": True,
                "metadata": {
                    "fixture": fixture,
                    "reference_path": str(reference_path.resolve()),
                    "reference_sha256": sha256_file(reference_path),
                    "reference_scenario_ids": [item["id"] for item in reference],
                    "runtime": runtime_metadata(),
                    "database": db_name,
                    "client": "PHP PDO persistent connection",
                    "connection_reuse": True,
                    "boundary": client.boundary,
                    "connect_ms": client.connect_ms,
                    "prepared_statement_policy": client.prepared_statement_policy,
                    "table_rows": client.table_rows,
                    "total_source_rows": client.total_source_rows,
                    "cache_policy": "uncontrolled-cache; warmed persistent connection",
                    "field_tolerances": {key: str(value) for key, value in DB_FIELD_TOLERANCES.items()},
                    "timing_mode": timing_mode,
                    "gc_policy": "collect before host-side continuation" if timing_mode == "gc-controlled" else "not forced",
                    "runs": runs,
                    "warmups": warmups,
                    "scenario_order": [item["id"] for item in reference],
                },
                "scenarios": [],
            }

            for expected in reference:
                scenario_id = expected["id"]
                plan = plans[scenario_id][dialect]
                sql = plan.sql_statement.as_statement("inline")
                failures: list[str] = []
                for warmup in range(warmups):
                    if timing_mode == "gc-controlled":
                        gc.collect()
                    print(
                        f"[database] {dialect} {scenario_id} warmup {warmup + 1}/{warmups}",
                        flush=True,
                    )
                    response = client.query(scenario_id, sql)
                    continuation_rows(plan, response["rows"])

                samples: list[dict[str, float]] = []
                for run in range(runs):
                    if timing_mode == "gc-controlled":
                        gc.collect()
                    print(
                        f"[database] {dialect} {scenario_id} measured {run + 1}/{runs}",
                        flush=True,
                    )
                    hybrid_start = time.perf_counter()
                    response = client.query(scenario_id, sql)
                    final_rows, materialize_ms, continuation_ms = continuation_rows(
                        plan, response["rows"]
                    )
                    hybrid_total_ms = (time.perf_counter() - hybrid_start) * 1000.0
                    raw_outputs[dialect][scenario_id].append(final_rows)
                    db_ms = float(response["db_execute_fetch_ms"])
                    samples.append({
                        "db_execute_fetch_ms": db_ms,
                        "db_materialize_ms": materialize_ms,
                        "continuation_ms": continuation_ms,
                        "hybrid_total_ms": hybrid_total_ms,
                        "elapsed_ms": hybrid_total_ms,
                    })
                    ok, message = compare_rows_exact(
                        final_rows, expected["in_memory_rows"], dialect, "In-Memory SEL"
                    )
                    if not ok:
                        failures.append(f"run {run + 1}: {message}")

                scenario_report: dict[str, Any] = {
                    "id": scenario_id,
                    "rows": len(raw_outputs[dialect][scenario_id][-1]),
                    "samples": samples,
                    "statistics": {
                        phase: stats(sample[phase] for sample in samples)
                        for phase in (
                            "db_execute_fetch_ms",
                            "db_materialize_ms",
                            "continuation_ms",
                            "hybrid_total_ms",
                        )
                    },
                    "passed": not failures,
                    "parity": {"passed": not failures, "failures": failures},
                    "failures": failures,
                }
                report["scenarios"].append(scenario_report)
                scenario_reports[dialect][scenario_id] = scenario_report
            report["passed"] = all(item["passed"] for item in report["scenarios"])
            reports[dialect] = report
    finally:
        for client in clients.values():
            client.close()

    # Cross-engine parity is checked for every measured repetition, not only
    # the final result, so a transient database result cannot be hidden.
    if set(("postgresql", "mariadb")).issubset(dialects):
        for expected in reference:
            scenario_id = expected["id"]
            for run in range(runs):
                left = raw_outputs["postgresql"][scenario_id][run]
                right = raw_outputs["mariadb"][scenario_id][run]
                ok, message = compare_rows_exact(left, right, "PostgreSQL", "MariaDB")
                if not ok:
                    for dialect in ("postgresql", "mariadb"):
                        item = scenario_reports[dialect][scenario_id]
                        item["failures"].append(f"run {run + 1}: {message}")
                        item["passed"] = False
                        item["parity"]["passed"] = False
                        item["parity"]["failures"].append(f"run {run + 1}: {message}")

    for report in reports.values():
        report["passed"] = all(item["passed"] for item in report["scenarios"])
    return reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run corrected SEL database benchmarks")
    parser.add_argument("--dataset", type=Path, default=ROOT / "tools/scale-test/dataset-10x.json")
    parser.add_argument("--reference", type=Path, default=ROOT / "tools/scale-test/benchmark_results.json")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--timing-mode", choices=("steady-state", "gc-controlled"), default="steady-state")
    parser.add_argument("--only", default=None, help="comma-separated scenario ids")
    parser.add_argument("--database", default=None)
    parser.add_argument("--dialect", choices=("postgresql", "mariadb"), default=None)
    parser.add_argument("--output", type=Path, default=ROOT / "tools/scale-test/database_results_corrected.json")
    parser.add_argument(
        "--skip-lisp",
        action="store_true",
        help="compatibility option; this corrected runner never launches Lisp",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1 or args.warmups < 0:
        raise SystemExit("--runs must be positive and --warmups must be non-negative")
    dataset = resolve_path(args.dataset)
    reference_path = resolve_path(args.reference)
    output = resolve_path(args.output)
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        reference = [item for item in reference if item["id"] in wanted]
        if not reference:
            raise SystemExit("--only selected no scenarios")

    print(
        f"Starting corrected database benchmark: {args.runs} measured + "
        f"{args.warmups} warmups per scenario.",
        flush=True,
    )
    reports = run_corrected_database_benchmark(
        dataset,
        reference_path,
        args.runs,
        args.warmups,
        args.timing_mode,
        reference=reference,
        database=args.database,
        dialects=(args.dialect,) if args.dialect else ("postgresql", "mariadb"),
    )
    artifact = {
        "schema_version": 2,
        "metadata": {
            "fixture": fixture_metadata(dataset, json.loads(dataset.read_text(encoding="utf-8"))),
            "reference_path": str(reference_path.resolve()),
            "reference_sha256": sha256_file(reference_path),
            "reference_scenario_ids": [item["id"] for item in reference],
            "runs": args.runs,
            "warmups": args.warmups,
            "timing_mode": args.timing_mode,
            "scenario_order": [item["id"] for item in reference],
            "lanes": list(reports),
        },
        "lanes": reports,
        "passed": all(report["passed"] for report in reports.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for dialect, report in reports.items():
        print(f"{dialect}: {'PASS' if report['passed'] else 'FAIL'}", flush=True)
    print(f"Machine-readable report: {output}")
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        raise SystemExit(1)
