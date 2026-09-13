#!/usr/bin/env python3
"""Python scale/parity runner for the Lisp benchmark oracle."""

from __future__ import annotations

import argparse
import gc
import json
import struct
import sys
import time
from pathlib import Path
from typing import Any

# Keep the runner directly executable from a source checkout, like the other
# host-specific scale runners.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python'))

from sel import Value, compile
from sel.registry import define
from sel.sql import Binding, HybridPlan, Sql
from sel.value import structural_hash

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_support import fixture_metadata, resolve_path, runtime_metadata, sha256_file, stats


def read_json(path: str | Path, *, parse_float=None):
    with resolve_path(path).open(encoding='utf-8') as stream:
        return json.load(stream, parse_float=parse_float)


def parse_whole(text: str, fallback: int) -> int:
    text = str(text).strip()
    head = text.split('.', 1)[0]
    try:
        return int(head)
    except ValueError:
        return fallback


def register_benchmark_builtins() -> None:
    define(
        'CUSTOM_VIP_SCORE', 2, 2,
        fn=lambda args, ctx: Value.int(
            (100 if args.text(0) == 'PLATINUM' else
             50 if args.text(0) == 'GOLD' else
             25 if args.text(0) == 'SILVER' else 10)
            + (2026 - parse_whole(args.text(1), 2024)) * 5),
    )
    define(
        'HOST_RISK_SCORE', 2, 2,
        fn=lambda args, ctx: Value.int(
            (30 if args.text(0) == 'US' else 10)
            + parse_whole(args.text(1), 0) * 2),
    )


def float32(value: str | int | float) -> float:
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


def load_context(dataset: dict[str, Any]) -> Value:
    context = Value.none()
    for table, rows in dataset.items():
        context.set(str(table).upper(), Value.from_native(rows))
    customers = context.get('CUSTOMERS')
    if customers is not None:
        shaped_customers = []
        customer_values = (
            customers.storage
            if customers.storage is not None
            else [customer for _, customer in customers.entries()]
        )
        for customer in customer_values:
            latitude = float32(customer.get('latitude').as_text())
            longitude = float32(customer.get('longitude').as_text())
            dx = longitude - 13.404954
            dy = latitude - 52.520008
            distance = (dx * dx + dy * dy) ** 0.5
            if customer.shape is not None and customer.storage is not None:
                keys = list(customer.shape.keys)
                values = list(customer.storage)
                keys.append('dist_berlin')
                values.append(Value.text(f'{distance:.6f}'))
                shaped_customers.append(Value.shaped(keys, values))
            else:
                entries = customer.entries()
                entries.append(('dist_berlin', Value.text(f'{distance:.6f}')))
                shaped_customers.append(Value.from_entries(entries))
        context.set('CUSTOMERS', Value.list(shaped_customers))
    return context


def relation(table: str, fields: dict[str, str]) -> Binding:
    mapped = {}
    text_fields = {'CODE', 'NAME', 'SKU', 'COUNTRY', 'TIER', 'STATUS'}
    for key, column in fields.items():
        mapped[key] = Binding.column(column, table,
                                     'TEXT' if key in text_fields else 'NUM')
    return Binding.relation(table, table, mapped)


def schema(dialect: str) -> dict[str, Binding]:
    categories = relation('categories', {
        'ID': 'id', 'CODE': 'code', 'NAME': 'name', 'VAT_RATE': 'vat_rate',
    })
    products = relation('products', {
        'ID': 'id', 'SKU': 'sku', 'NAME': 'name', 'CATEGORY_ID': 'category_id',
        'PRICE': 'price', 'IS_ACTIVE': 'is_active',
    })
    customer_fields = {
        'ID': Binding.column('id', 'customers', 'NUM'),
        'NAME': Binding.column('name', 'customers', 'TEXT'),
        'TIER': Binding.column('tier', 'customers', 'TEXT'),
        'COUNTRY': Binding.column('country', 'customers', 'TEXT'),
        'CREATED_YEAR': Binding.column('created_year', 'customers', 'NUM'),
        'LATITUDE': Binding.column('latitude', 'customers', 'NUM'),
        'LONGITUDE': Binding.column('longitude', 'customers', 'NUM'),
        'DIST_BERLIN': Binding.raw(
            'ROUND((customers.location <-> point(13.404954, 52.520008))::numeric, 6)',
            'NUM') if dialect == 'postgresql' else Binding.raw(
                'ROUND(ST_Distance(POINT(customers.longitude, customers.latitude), '
                'POINT(13.404954, 52.520008)), 6)', 'NUM'),
    }
    return {
        'CATEGORIES': categories,
        'PRODUCTS': products,
        'CUSTOMERS': Binding.relation('customers', 'customers', customer_fields),
        'ORDERS': relation('orders', {
            'ID': 'id', 'CUSTOMER_ID': 'customer_id', 'STATUS': 'status',
            'DISCOUNT': 'discount', 'ORDER_YEAR': 'order_year',
        }),
        'ORDER_ITEMS': relation('order_items', {
            'ID': 'id', 'ORDER_ID': 'order_id', 'PRODUCT_ID': 'product_id',
            'QUANTITY': 'quantity', 'UNIT_PRICE': 'unit_price',
        }),
    }


def canonical(value: Any) -> Any:
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    return value


def benchmark_value(value: Value) -> Any:
    value.force()
    if value.size() == 0:
        if value.is_list:
            return []
        if value.kind in (Value.TEXT, Value.BIN, Value.BOOL):
            return value.scalar
        return None
    if value.is_list:
        return [benchmark_value(item) for item in value.values()]
    out = {key: benchmark_value(child) for key, child in value.entries()}
    if value.kind != Value.NONE:
        out['_'] = value.scalar
    return out


def same_value(actual: Any, expected: Any) -> bool:
    return canonical(actual) == canonical(expected)


def representation_counts(value: Value, counts: dict[str, int]) -> None:
    value.force()
    if value.is_list:
        counts['lists'] += 1
    elif value.shape is not None:
        counts['shaped_records'] += 1
    elif value.size() > 0:
        counts['fallback_records'] += 1
    if value.storage is not None:
        for child in value.storage:
            representation_counts(child, counts)
    elif value.children:
        for child in value.children.values():
            representation_counts(child, counts)


def run_and_materialize(program, context: Value) -> tuple[float, float, float, Any]:
    prepared_start = time.perf_counter()
    run_start = time.perf_counter()
    actual = program.run(context)
    run_ms = (time.perf_counter() - run_start) * 1000.0
    materialize_start = time.perf_counter()
    rows = benchmark_value(actual)
    materialize_ms = (time.perf_counter() - materialize_start) * 1000.0
    prepared_ms = (time.perf_counter() - prepared_start) * 1000.0
    return run_ms, materialize_ms, prepared_ms, rows


def plan_sql(plan: HybridPlan) -> str | None:
    return None if plan.sql_statement is None else plan.sql_statement.as_statement('inline')


def check_plan(plan: HybridPlan, expected: dict[str, Any], dialect: str,
               failures: list[str]) -> None:
    sql_key = 'sql_postgres' if dialect == 'postgresql' else 'sql_mariadb'
    if plan_sql(plan) != expected.get(sql_key):
        failures.append(f'{dialect} SQL differs from Lisp reference')
    expected_hybrid = (expected.get('is_hybrid') is True
                       or expected.get('has_continuation') is True)
    if plan.is_hybrid != expected_hybrid:
        failures.append(f'{dialect} hybrid flag differs')
    if plan.pure_sql != (not expected_hybrid):
        failures.append(f'{dialect} pureSql flag differs')
    if (plan.continuation_program is not None) != (expected.get('has_continuation') is True):
        failures.append(f'{dialect} continuation presence differs')


def main() -> int:
    parser = argparse.ArgumentParser(description='SEL Python scale/parity runner')
    parser.add_argument('--dataset', default='tools/scale-test/dataset-10x.json')
    parser.add_argument('--reference', default='tools/scale-test/benchmark_results.json')
    parser.add_argument('--only', default=None)
    parser.add_argument('--output', default=None)
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--warmups', type=int, default=0)
    parser.add_argument('--timing-mode', default='steady-state',
                        choices=('steady-state', 'gc-controlled'))
    args = parser.parse_args()
    if args.runs < 1 or args.warmups < 0:
        raise SystemExit('--runs must be positive and --warmups must be non-negative')

    register_benchmark_builtins()
    dataset_path = resolve_path(args.dataset)
    reference_path = resolve_path(args.reference)
    dataset = read_json(dataset_path, parse_float=str)
    reference = read_json(reference_path)
    if not isinstance(reference, list):
        raise SystemExit('reference must be a scenario list')
    reference_ids = [str(item['id']) for item in reference]
    if len(reference_ids) != len(set(reference_ids)):
        raise SystemExit('reference contains duplicate scenario ids')
    selected = reference if args.only is None else [
        item for item in reference if item['id'] in set(args.only.split(','))
    ]
    if not selected:
        raise SystemExit('--only selected no scenarios')
    context = load_context(dataset)
    fixture = fixture_metadata(dataset_path, dataset)
    counts = {'shaped_records': 0, 'fallback_records': 0, 'lists': 0}
    representation_counts(context, counts)
    context_signature = structural_hash(context)

    compile_ms: dict[str, float] = {}
    programs = {}
    plans = {}
    for expected in selected:
        started = time.perf_counter()
        programs[expected['id']] = compile(expected['query'])
        compile_ms[expected['id']] = (time.perf_counter() - started) * 1000.0
        failures: list[str] = []
        plans[expected['id']] = {
            dialect: Sql.plan_hybrid(programs[expected['id']], dialect, schema(dialect))
            for dialect in ('postgresql', 'mariadb')
        }
        for dialect, plan in plans[expected['id']].items():
            check_plan(plan, expected, dialect, failures)
        if failures:
            raise RuntimeError(f"python SQL parity failed for {expected['id']}: {'; '.join(failures)}")

    report = {
        'schema_version': 2,
        'implementation': 'python',
        'passed': True,
        'metadata': {
            'fixture': fixture,
            'reference_path': str(reference_path.resolve()),
            'reference_sha256': sha256_file(reference_path),
            'reference_scenario_ids': [item['id'] for item in selected],
            'runtime': runtime_metadata(),
            'representation': counts,
            'scenario_order': [item['id'] for item in selected],
            'timing_mode': args.timing_mode,
            'gc_policy': 'gc.collect() before warmups and samples' if args.timing_mode == 'gc-controlled' else 'not forced',
            'runs': args.runs,
            'warmups': args.warmups,
        },
        'scenarios': [],
    }

    for expected in selected:
        scenario_id = expected['id']
        failures: list[str] = []
        # The validation execution is intentionally outside all reported phase
        # timers. It proves that this read-only fixture can be reused safely.
        before = structural_hash(context)
        _, _, _, validation_rows = run_and_materialize(programs[scenario_id], context)
        after = structural_hash(context)
        context_unchanged = before == after == context_signature
        if not context_unchanged:
            failures.append('prepared context changed during untimed validation')
        if not same_value(validation_rows, expected['in_memory_rows']):
            failures.append('in-memory rows differ from Lisp reference during validation')

        for warmup in range(args.warmups):
            if args.timing_mode == 'gc-controlled':
                gc.collect()
            print(f'[python] {scenario_id} warmup {warmup + 1}/{args.warmups}', flush=True)
            _, _, _, warm_rows = run_and_materialize(programs[scenario_id], context)
            if not same_value(warm_rows, expected['in_memory_rows']):
                failures.append(f'warmup {warmup + 1} result differs from Lisp reference')
        samples: list[dict[str, float]] = []
        for run in range(args.runs):
            if args.timing_mode == 'gc-controlled':
                gc.collect()
            print(f'[python] {scenario_id} measured {run + 1}/{args.runs}', flush=True)
            run_ms, materialize_ms, prepared_ms, actual_rows = run_and_materialize(
                programs[scenario_id], context)
            if not same_value(actual_rows, expected['in_memory_rows']):
                failures.append(f'measured run {run + 1} result differs from Lisp reference')
            if structural_hash(context) != context_signature:
                failures.append(f'measured run {run + 1} changed prepared context')
            samples.append({
                'program_run_ms': run_ms,
                'materialize_ms': materialize_ms,
                'prepared_total_ms': prepared_ms,
                'elapsed_ms': prepared_ms,
            })
        phase_stats = {
            phase: stats([sample[phase] for sample in samples])
            for phase in ('program_run_ms', 'materialize_ms', 'prepared_total_ms')
        }
        passed = not failures
        report['passed'] = report['passed'] and passed
        report['scenarios'].append({
            'id': scenario_id,
            'rows': len(validation_rows) if isinstance(validation_rows, list) else None,
            'compile_ms': compile_ms[scenario_id],
            'samples': samples,
            'statistics': phase_stats,
            'parity': {'passed': passed, 'failures': failures},
            'context_unchanged': context_unchanged,
            'passed': passed,
            'failures': failures,
        })

    if args.output:
        output = resolve_path(args.output)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"Python scale parity: {sum(item['passed'] for item in report['scenarios'])}/"
          f"{len(report['scenarios'])} passed; mode={args.timing_mode}; runs={args.runs}; warmups={args.warmups}")
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
