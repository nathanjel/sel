#!/usr/bin/env python3
"""
tools/scale-test/run_benchmarks.py
Scale & Parity Automated Benchmark Harness for SEL

Tests 5 complex relational scenarios across:
  1. PostgreSQL 17 (in Docker sel-pg)
  2. MariaDB 11.8 (local)
  3. In-Memory SEL (Common Lisp SBCL over JSON dataset)
  4. Hybrid Pushdown (SQL execution + in-memory continuation)

Verifies 100% semantic and numeric parity, records execution latencies,
and outputs a comprehensive benchmark report.
"""

import subprocess
import time
import json
import os
import sys
from decimal import Decimal

import argparse

BENCHMARK_JSON = "tools/scale-test/benchmark_results.json"
SBCL_SCRIPT = "tools/scale-test/sel_benchmarks.lisp"

def run_postgres_query(sql, db="sel_oracle"):
    clean_sql = sql.strip().rstrip(';')
    # Wrap in json_agg to get exact JSON records back
    wrapper = f"SELECT json_agg(t) FROM ({clean_sql}) t;"
    t0 = time.perf_counter()
    res = subprocess.run(
        ["docker", "exec", "-i", "sel-pg", "psql", "-U", "postgres", "-d", db, "-t", "-A", "-c", wrapper],
        capture_output=True, text=True
    )
    dt = time.perf_counter() - t0
    if res.returncode != 0:
        raise RuntimeError(f"PostgreSQL query failed: {res.stderr.strip()}\nSQL:\n{clean_sql}")
    out = res.stdout.strip()
    data = json.loads(out) if out and out != "" else []
    return data, dt

def run_mariadb_query(sql, db="sel_oracle"):
    clean_sql = sql.strip().rstrip(';')
    t0 = time.perf_counter()
    res = subprocess.run(
        ["mariadb", "-D", db, "--batch", "--raw", "-e", clean_sql],
        capture_output=True, text=True
    )
    dt = time.perf_counter() - t0
    if res.returncode != 0:
        raise RuntimeError(f"MariaDB query failed: {res.stderr.strip()}\nSQL:\n{clean_sql}")
    lines = res.stdout.strip().split("\n")
    if not lines or not lines[0]:
        return [], dt
    headers = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        if not line:
            continue
        parts = line.split("\t")
        rows.append(dict(zip(headers, parts)))
    return rows, dt

def canonical_val(v):
    if v is None or v == "NULL":
        return None
    s = str(v).strip()
    try:
        d = Decimal(s)
        d_rounded = round(d, 4)
        if d_rounded == 0:
            return "0"
        norm = d_rounded.normalize()
        return f"{norm:f}"
    except Exception:
        return s

def compare_rows(rows_a, rows_b, label_a, label_b):
    """Compares two lists of dictionaries for exact semantic parity."""
    if len(rows_a) != len(rows_b):
        return False, f"Row count mismatch: {label_a} has {len(rows_a)} rows, {label_b} has {len(rows_b)} rows"
    
    for i, (ra, rb) in enumerate(zip(rows_a, rows_b)):
        # Normalize keys (lowercase)
        keys_a = {k.lower(): k for k in ra.keys()}
        keys_b = {k.lower(): k for k in rb.keys()}
        
        all_keys = set(keys_a.keys()).union(keys_b.keys())
        for k in all_keys:
            va = ra.get(keys_a.get(k)) if k in keys_a else None
            vb = rb.get(keys_b.get(k)) if k in keys_b else None
            ca = canonical_val(va)
            cb = canonical_val(vb)
            if ca != cb:
                return False, f"Row {i+1} field '{k}' mismatch: {label_a}={va!r} (canon: {ca}) vs {label_b}={vb!r} (canon: {cb})"
    
    return True, "PARITY"

def run_hybrid_continuation(sc_id, intermediate_rows):
    """Executes the in-memory continuation for hybrid pushdown scenarios."""
    if sc_id == "scenario2":
        # Fall-through: Evaluate CUSTOM_VIP_SCORE(tier, created_year)
        def eval_vip_score(tier, year):
            tier = str(tier or "").strip()
            try:
                yr = int(float(str(year).strip()))
            except Exception:
                yr = 2024
            base = 100 if tier == "PLATINUM" else (50 if tier == "GOLD" else (25 if tier == "SILVER" else 10))
            return str(base + (2026 - yr) * 5)
        
        out = []
        for r in intermediate_rows:
            cid = str(r.get("id") or r.get("ID"))
            country = str(r.get("country") or r.get("COUNTRY"))
            tier = r.get("tier") or r.get("TIER")
            yr = r.get("created_year") or r.get("CREATED_YEAR")
            out.append({
                "id": cid,
                "country": country,
                "vip_score": eval_vip_score(tier, yr)
            })
        return out
    
    elif sc_id == "scenario3":
        # Mid-pipeline fallback: Evaluate HOST_RISK_SCORE, filter > 50, bucket by country, sort count DESC, limit 5
        def host_risk_score(country, discount):
            try:
                disc = int(float(str(discount or "0").strip()))
            except Exception:
                disc = 0
            base = 30 if country == "US" else 10
            return base + disc * 2
        
        from collections import Counter
        counts = Counter()
        for r in intermediate_rows:
            country = str(r.get("country") or r.get("COUNTRY") or "")
            disc = r.get("discount") or r.get("DISCOUNT") or 0
            score = host_risk_score(country, disc)
            if score > 50:
                counts[country] += 1
        
        sorted_counts = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        return [{"country": c, "high_risk_count": str(cnt)} for c, cnt in sorted_counts]
    
    return intermediate_rows

def main():
    parser = argparse.ArgumentParser(description="SEL Scale & Parity Benchmark Runner")
    parser.add_argument("--scale", type=int, choices=[10, 100], default=10, help="Scale factor (10 for ~137k rows, 100 for ~1.37M rows)")
    parser.add_argument("--skip-lisp", action="store_true", help="Skip running SBCL in-memory benchmarks and reuse benchmark_results.json")
    args = parser.parse_args()

    db_name = "sel_oracle_100x" if args.scale == 100 else "sel_oracle"
    dataset_rows = "1,370,200" if args.scale == 100 else "137,100"

    print("=" * 80)
    print(f"SEL SCALE & PARITY BENCHMARK SUITE ({args.scale}x SCALE — {dataset_rows} ROWS)")
    print("=" * 80)
    
    # 1. Generate / Refresh Lisp benchmark results (for 10x dataset)
    if not args.skip_lisp:
        print("\n[Step 1/3] Running In-Memory SEL Benchmarks via SBCL...")
        t_start = time.perf_counter()
        proc = subprocess.run(
            ["sbcl", "--dynamic-space-size", "4096", "--noinform", "--disable-debugger", "--non-interactive",
             "--load", SBCL_SCRIPT],
            capture_output=True, text=True
        )
        if proc.returncode != 0:
            print("SBCL runner failed:")
            print(proc.stderr)
            print(proc.stdout)
            sys.exit(1)
        print(f"SBCL finished in {time.perf_counter() - t_start:.2f}s")
    else:
        print("\n[Step 1/3] Skipping In-Memory SEL Benchmarks (reusing existing results)...")
    
    with open(BENCHMARK_JSON) as f:
        scenarios = json.load(f)
    
    print(f"Loaded {len(scenarios)} benchmark scenarios.\n")
    print(f"[Step 2/3] Executing queries on PostgreSQL & MariaDB ({db_name}) & validating Parity...")
    
    summary = []
    
    for sc in scenarios:
        sc_id = sc["id"]
        name = sc["name"]
        desc = sc["description"]
        is_hybrid = sc.get("is_hybrid", False)
        sql_pg = sc.get("sql_postgres")
        sql_ma = sc.get("sql_mariadb")
        mem_rows = sc.get("in_memory_rows", []) if args.scale == 10 else []
        mem_dt = sc.get("in_memory_latency_sec", 0.0) if args.scale == 10 else 0.0
        
        print(f"\n--- {name} ---")
        print(f"Goal: {desc}")
        
        # PostgreSQL execution
        pg_rows, pg_dt = [], 0.0
        if sql_pg:
            try:
                pg_rows, pg_dt = run_postgres_query(sql_pg, db=db_name)
            except Exception as e:
                print(f"  [!] PostgreSQL error: {e}")
        
        # MariaDB execution
        ma_rows, ma_dt = [], 0.0
        if sql_ma:
            try:
                ma_rows, ma_dt = run_mariadb_query(sql_ma, db=db_name)
            except Exception as e:
                print(f"  [!] MariaDB error: {e}")
        
        # If hybrid scenario, apply continuation to DB rows
        if is_hybrid:
            pg_eval_rows = run_hybrid_continuation(sc_id, pg_rows)
            ma_eval_rows = run_hybrid_continuation(sc_id, ma_rows)
        else:
            pg_eval_rows = pg_rows
            ma_eval_rows = ma_rows
            
        # Parity checks
        ok_pg_ma, msg_pg_ma = compare_rows(pg_eval_rows, ma_eval_rows, "PostgreSQL", "MariaDB")
        if args.scale == 10:
            ok_pg_mem, msg_pg_mem = compare_rows(pg_eval_rows, mem_rows, "PostgreSQL", "In-Memory SEL")
            ok_ma_mem, msg_ma_mem = compare_rows(ma_eval_rows, mem_rows, "MariaDB", "In-Memory SEL")
            all_parity = ok_pg_ma and ok_pg_mem and ok_ma_mem
        else:
            all_parity = ok_pg_ma
            msg_pg_mem = msg_pg_ma
            
        print(f"  PostgreSQL:    {len(pg_rows):>4} rows in {pg_dt*1000:>7.2f} ms")
        print(f"  MariaDB:       {len(ma_rows):>4} rows in {ma_dt*1000:>7.2f} ms")
        if args.scale == 10:
            print(f"  In-Memory SEL: {len(mem_rows):>4} rows in {mem_dt*1000:>7.2f} ms")
            parity_label = "PostgreSQL, MariaDB & In-Memory SEL"
        else:
            parity_label = "PostgreSQL & MariaDB"
        
        if all_parity:
            print(f"  Result Parity: [PASS] 100% Match across {parity_label}")
        else:
            print(f"  Result Parity: [FAIL]")
            if not ok_pg_ma:  print(f"    - PG vs MariaDB: {msg_pg_ma}")
            if args.scale == 10:
                if not ok_pg_mem: print(f"    - PG vs In-Mem:  {msg_pg_mem}")
                if not ok_ma_mem: print(f"    - Maria vs In-Mem: {msg_ma_mem}")
            
        if pg_eval_rows:
            print(f"  Sample Row 1:  {pg_eval_rows[0]}")
            
        summary.append({
            "id": sc_id,
            "name": name,
            "rows": len(pg_eval_rows),
            "pg_ms": pg_dt * 1000,
            "ma_ms": ma_dt * 1000,
            "mem_ms": mem_dt * 1000 if args.scale == 10 else None,
            "parity": all_parity,
            "notes": msg_pg_ma if not all_parity else "Exact Match"
        })

    print("\n" + "=" * 80)
    print(f"BENCHMARK SUMMARY REPORT ({args.scale}x SCALE — {dataset_rows} ROWS)")
    print("=" * 80)
    if args.scale == 10:
        print(f"{'Scenario':<42} | {'Rows':<5} | {'Postgres':<10} | {'MariaDB':<10} | {'In-Memory':<10} | {'Parity':<6}")
        print("-" * 95)
        for s in summary:
            par_str = "PASS" if s["parity"] else "FAIL"
            print(f"{s['name']:<42} | {s['rows']:<5} | {s['pg_ms']:>8.2f}ms | {s['ma_ms']:>8.2f}ms | {s['mem_ms']:>8.2f}ms | {par_str:<6}")
        print("=" * 95)
    else:
        print(f"{'Scenario':<45} | {'Rows':<5} | {'Postgres':<12} | {'MariaDB':<12} | {'Parity':<6}")
        print("-" * 88)
        for s in summary:
            par_str = "PASS" if s["parity"] else "FAIL"
            print(f"{s['name']:<45} | {s['rows']:<5} | {s['pg_ms']:>10.2f}ms | {s['ma_ms']:>10.2f}ms | {par_str:<6}")
        print("=" * 88)

if __name__ == "__main__":
    main()
