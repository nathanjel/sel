#!/usr/bin/env python3
"""Validate and time Rust Scenario 1 against an independent Decimal join/group oracle."""
import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]

def expected_rows(data):
    items = defaultdict(list)
    for item in data['order_items']:
        items[item['order_id']].append(item)
    products = {p['id']: p for p in data['products']}
    categories = {c['id']: c for c in data['categories']}
    totals = {}
    for order in data['orders']:
        if order['status'] != 'COMPLETED' or order['order_year'] < 2025:
            continue
        for item in items[order['id']]:
            product = products.get(item['product_id'])
            if product is None or product['is_active'] != 1:
                continue
            net = Decimal(item['unit_price']) * item['quantity'] - Decimal(order['discount'])
            if net <= 10:
                continue
            category = categories.get(product['category_id'])
            if category is None:
                continue
            name = category['name']
            count, total = totals.get(name, (0, Decimal('0')))
            totals[name] = (count + 1, total + net)
    rows = [(name, count, total) for name, (count, total) in totals.items() if total >= 500]
    rows.sort(key=lambda row: row[2], reverse=True)
    return [{'category': name, 'item_lines': str(count), 'total_net': str(total)}
            for name, count, total in rows[:10]]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default='tools/scale-test/dataset-10x.json')
    parser.add_argument('--binary', default='rust/target/release/scale_bench')
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.runs < 1 or args.warmups < 0:
        parser.error('runs must be positive and warmups nonnegative')
    dataset_path = ROOT / args.dataset
    raw = dataset_path.read_bytes()
    data = json.loads(raw)
    expected = expected_rows(data)
    result = subprocess.run([str(ROOT / args.binary), str(dataset_path),
        str(ROOT / 'tools/scale-test/benchmark_results.json'), str(args.runs), str(args.warmups)],
        check=True, text=True, capture_output=True)
    report = json.loads(result.stdout)
    if report['rows'] != expected:
        raise RuntimeError(f"Rust result differs from Decimal oracle:\nactual={report['rows']}\nexpected={expected}")
    times = [s['prepared_total_ms'] for s in report['samples']]
    report.update({'validated': True, 'oracle': 'independent Python Decimal joins/grouping',
        'dataset_sha256': hashlib.sha256(raw).hexdigest(),
        'table_rows': {k: len(v) for k, v in data.items()},
        'platform': platform.platform(), 'median_prepared_ms': statistics.median(times),
        'binary_sha256': hashlib.sha256((ROOT / args.binary).read_bytes()).hexdigest(),
        'rustc': subprocess.check_output(['rustc', '--version'], text=True).strip(),
        'min_prepared_ms': min(times), 'target_ms': 550,
        'target_met': statistics.median(times) <= 550})
    rendered = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + '\n')
    print(rendered)

if __name__ == '__main__':
    main()
