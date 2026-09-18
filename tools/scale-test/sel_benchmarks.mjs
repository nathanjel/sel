#!/usr/bin/env node
// JavaScript scale/parity runner.
//
// benchmark_results.json is produced by sel_benchmarks.lisp and is the Lisp
// reference for this lane.  This runner deliberately compares both the
// in-memory result and the two generated SQL statements against that oracle;
// database execution remains the responsibility of run_benchmarks.py.

import fs from 'node:fs';
import crypto from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';

import {
  Value,
  compile,
  register,
} from '../../js/src/sel.mjs';
import { structuralHash } from '../../js/src/value.mjs';
import { Binding, planHybrid } from '../../js/src/sql/index.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index < 0 ? fallback : process.argv[index + 1] ?? fallback;
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(path.resolve(ROOT, file), 'utf8'));
}

function resolvePath(file) {
  return path.resolve(ROOT, file);
}

function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(resolvePath(file))).digest('hex');
}

function fixtureMetadata(file, dataset) {
  const resolved = resolvePath(file);
  const sha256 = crypto.createHash('sha256').update(fs.readFileSync(resolved)).digest('hex');
  const tableRows = Object.fromEntries(Object.entries(dataset).map(([name, rows]) => [
    name.toUpperCase(), Array.isArray(rows) ? rows.length : null,
  ]));
  return {
    path: resolved,
    sha256,
    table_rows: tableRows,
    total_source_rows: Object.values(tableRows).filter((count) => count !== null)
      .reduce((total, count) => total + count, 0),
    schema_version: 1,
  };
}

function runtimeMetadata() {
  return {
    os: `${os.type()} ${os.release()}`,
    machine: os.arch(),
    cpu: os.cpus()[0]?.model ?? 'unknown',
    logical_cpus: os.cpus().length,
    available_memory_bytes: os.freemem(),
    node: process.version,
    v8: process.versions.v8,
    exec_argv: process.execArgv,
  };
}

function phaseStats(samples) {
  if (samples.length === 0) throw new Error('cannot summarize empty samples');
  const values = samples.map(Number);
  if (values.some((value) => !Number.isFinite(value) || value < 0)) {
    throw new Error(`invalid benchmark sample: ${values}`);
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const median = sorted.length % 2 === 1
    ? sorted[(sorted.length - 1) / 2]
    : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
  const variance = values.length > 1
    ? values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1)
    : 0;
  const rank = Math.max(1, Math.ceil(0.95 * values.length));
  return {
    count: values.length,
    mean_ms: mean,
    median_ms: median,
    min_ms: sorted[0],
    max_ms: sorted[sorted.length - 1],
    stdev_ms: Math.sqrt(variance),
    cv: mean === 0 ? 0 : Math.sqrt(variance) / mean,
    p95_ms: values.length >= 20 ? sorted[rank - 1] : null,
    samples_ms: values,
  };
}

function parseWhole(text, fallback) {
  const value = Number.parseInt(String(text).split('.')[0], 10);
  return Number.isFinite(value) ? value : fallback;
}

function registerBenchmarkBuiltins() {
  register({
    name: 'CUSTOM_VIP_SCORE', min: 2, max: 2, overwrite: true,
    fn: (args) => {
      const tier = args.val(0).asText(args.posOf(0));
      const year = parseWhole(args.val(1).asText(args.posOf(1)), 2024);
      const base = tier === 'PLATINUM' ? 100
        : tier === 'GOLD' ? 50
          : tier === 'SILVER' ? 25 : 10;
      return Value.int(base + (2026 - year) * 5);
    },
  });
  register({
    name: 'HOST_RISK_SCORE', min: 2, max: 2, overwrite: true,
    fn: (args) => {
      const country = args.val(0).asText(args.posOf(0));
      const discount = parseWhole(args.val(1).asText(args.posOf(1)), 0);
      return Value.int((country === 'US' ? 30 : 10) + discount * 2);
    },
  });
}

function loadContext(dataset) {
  const context = Value.none();
  for (const [table, rows] of Object.entries(dataset)) {
    context.set(table.toUpperCase(), Value.fromNative(rows));
  }

  // The Lisp fixture uses READ-FROM-STRING, whose default is a single-float
  // for these values.  Math.fround keeps the JS oracle comparison on the same
  // six-decimal boundary without changing SEL's exact decimal arithmetic.
  const customers = context.get('CUSTOMERS');
  if (customers) {
    const shapedCustomers = [];
    const customerValues = customers.storage ?? customers.values();
    for (const customer of customerValues) {
      const latitude = Math.fround(Number(customer.get('latitude').asText()));
      const longitude = Math.fround(Number(customer.get('longitude').asText()));
      const dx = longitude - 13.404954;
      const dy = latitude - 52.520008;
      const distance = Math.sqrt(dx * dx + dy * dy);
      if (customer.shape !== null && customer.storage !== null) {
        const keys = [...customer.shape.keys, 'dist_berlin'];
        const values = [...customer.storage, Value.text(distance.toFixed(6))];
        shapedCustomers.push(Value.shapedOwned(keys, values));
      } else {
        const entries = customer.entries();
        entries.push(['dist_berlin', Value.text(distance.toFixed(6))]);
        shapedCustomers.push(Value.fromEntries(entries));
      }
    }
    context.set('CUSTOMERS', Value.list(shapedCustomers));
  }
  return context;
}

function relation(name, fields) {
  const mapped = {};
  for (const [key, column] of Object.entries(fields)) {
    mapped[key] = Binding.column(column, name, key === 'CODE' || key === 'NAME'
      || key === 'SKU' || key === 'COUNTRY' || key === 'TIER' || key === 'STATUS'
      ? 'TEXT' : 'NUM');
  }
  return Binding.relation(name, name, mapped);
}

function schema(dialect) {
  const categories = relation('categories', {
    ID: 'id', CODE: 'code', NAME: 'name', VAT_RATE: 'vat_rate',
  });
  const products = relation('products', {
    ID: 'id', SKU: 'sku', NAME: 'name', CATEGORY_ID: 'category_id',
    PRICE: 'price', IS_ACTIVE: 'is_active',
  });
  const customersFields = {
    ID: Binding.column('id', 'customers', 'NUM'),
    NAME: Binding.column('name', 'customers', 'TEXT'),
    TIER: Binding.column('tier', 'customers', 'TEXT'),
    COUNTRY: Binding.column('country', 'customers', 'TEXT'),
    CREATED_YEAR: Binding.column('created_year', 'customers', 'NUM'),
    LATITUDE: Binding.column('latitude', 'customers', 'NUM'),
    LONGITUDE: Binding.column('longitude', 'customers', 'NUM'),
    DIST_BERLIN: dialect === 'postgresql'
      ? Binding.raw('ROUND((customers.location <-> point(13.404954, 52.520008))::numeric, 6)', 'NUM')
      : Binding.raw('ROUND(ST_Distance(POINT(customers.longitude, customers.latitude), POINT(13.404954, 52.520008)), 6)', 'NUM'),
  };
  const customers = Binding.relation('customers', 'customers', customersFields);
  const orders = relation('orders', {
    ID: 'id', CUSTOMER_ID: 'customer_id', STATUS: 'status',
    DISCOUNT: 'discount', ORDER_YEAR: 'order_year',
  });
  const orderItems = relation('order_items', {
    ID: 'id', ORDER_ID: 'order_id', PRODUCT_ID: 'product_id',
    QUANTITY: 'quantity', UNIT_PRICE: 'unit_price',
  });
  return {
    CATEGORIES: categories,
    PRODUCTS: products,
    CUSTOMERS: customers,
    ORDERS: orders,
    ORDER_ITEMS: orderItems,
  };
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function benchmarkValue(value) {
  if (value.size() === 0) {
    if (value.isList) return [];
    if (value.kind === 'TEXT' || value.kind === 'BIN' || value.kind === 'BOOL') return value.scalar;
    return null;
  }
  if (value.isList) return value.values().map(benchmarkValue);
  const out = {};
  for (const [key, child] of value.entries()) out[key] = benchmarkValue(child);
  if (value.kind === 'TEXT' || value.kind === 'BIN' || value.kind === 'BOOL') out._ = value.scalar;
  return out;
}

function representationCounts(value, counts) {
  if (value.isList) counts.lists += 1;
  else if (value.shape) counts.shaped_records += 1;
  else if (value.size() > 0) counts.fallback_records += 1;
  if (value.storage !== null) {
    for (const child of value.storage) representationCounts(child, counts);
  } else if (value.children) {
    for (const child of value.children.values()) representationCounts(child, counts);
  } else if (value._entries) {
    for (const [, child] of value._entries) representationCounts(child, counts);
  }
}

function runAndMaterialize(program, context) {
  const preparedStart = performance.now();
  const runStart = performance.now();
  const actual = program.run(context);
  const runMs = performance.now() - runStart;
  const materializeStart = performance.now();
  const rows = benchmarkValue(actual);
  const materializeMs = performance.now() - materializeStart;
  const preparedMs = performance.now() - preparedStart;
  return { runMs, materializeMs, preparedMs, rows };
}

function equalValues(actual, expected) {
  return JSON.stringify(canonical(actual)) === JSON.stringify(canonical(expected));
}

function planSql(plan) {
  return plan.sqlStatement === null ? null : plan.sqlStatement.asStatement('inline');
}

function checkPlan(plan, expected, dialect, failures) {
  const sqlKey = dialect === 'postgresql' ? 'sql_postgres' : 'sql_mariadb';
  const actualSql = planSql(plan);
  const expectedSql = expected[sqlKey] ?? null;
  if (actualSql !== expectedSql) {
    failures.push(`${dialect} SQL differs from Lisp reference`);
  }
  const expectedHasSql = expectedSql !== null;
  const expectedContinuation = expected.has_continuation === true;
  const expectedHybrid = expectedHasSql && expectedContinuation;
  const expectedPureSql = expectedHasSql && !expectedContinuation;
  if (plan.isHybrid !== expectedHybrid) {
    failures.push(`${dialect} hybrid flag is ${plan.isHybrid}, expected ${expectedHybrid}`);
  }
  if (plan.pureSql !== expectedPureSql) {
    failures.push(`${dialect} pureSql flag is ${plan.pureSql}, expected ${expectedPureSql}`);
  }
  const hasContinuation = plan.continuationProgram !== null;
  if (hasContinuation !== expectedContinuation) {
    failures.push(`${dialect} continuation presence differs from Lisp reference`);
  }
}

function correctedMain() {
  registerBenchmarkBuiltins();
  const datasetFile = option('--dataset', 'tools/scale-test/dataset-10x.json');
  const referenceFile = option('--reference', 'tools/scale-test/benchmark_results.json');
  const dataset = readJson(datasetFile);
  const reference = readJson(referenceFile);
  const only = option('--only', null);
  const runs = parseWhole(option('--runs', '1'), 1);
  const warmups = parseWhole(option('--warmups', '0'), 0);
  const timingMode = option('--timing-mode', 'steady-state');
  if (runs < 1 || warmups < 0) throw new Error('--runs must be positive and --warmups non-negative');
  if (!['steady-state', 'gc-controlled'].includes(timingMode)) {
    throw new Error(`unsupported timing mode: ${timingMode}`);
  }
  if (timingMode === 'gc-controlled' && typeof global.gc !== 'function') {
    throw new Error('gc-controlled mode requires Node.js --expose-gc');
  }
  const selected = only === null
    ? reference
    : reference.filter((scenario) => new Set(only.split(',')).has(scenario.id));
  if (selected.length === 0) throw new Error('--only selected no scenarios');
  const referenceIds = reference.map((scenario) => scenario.id);
  if (new Set(referenceIds).size !== referenceIds.length) throw new Error('reference has duplicate scenario ids');
  const context = loadContext(dataset);
  const representation = { shaped_records: 0, fallback_records: 0, lists: 0 };
  representationCounts(context, representation);
  const contextSignature = structuralHash(context);
  const programs = new Map();
  const compileMs = new Map();

  for (const expected of selected) {
    const compileStart = performance.now();
    const program = compile(expected.query);
    compileMs.set(expected.id, performance.now() - compileStart);
    programs.set(expected.id, program);
    const failures = [];
    for (const dialect of ['postgresql', 'mariadb']) {
      const plan = planHybrid(program, dialect, schema(dialect));
      checkPlan(plan, expected, dialect, failures);
    }
    if (failures.length > 0) throw new Error(`JS SQL parity failed for ${expected.id}: ${failures.join('; ')}`);
  }

  const report = {
    schema_version: 2,
    implementation: 'js',
    scenarios: [],
    passed: true,
    metadata: {
      fixture: fixtureMetadata(datasetFile, dataset),
      reference_path: resolvePath(referenceFile),
      reference_sha256: sha256File(referenceFile),
      reference_scenario_ids: selected.map((scenario) => scenario.id),
      runtime: runtimeMetadata(),
      representation,
      scenario_order: selected.map((scenario) => scenario.id),
      timing_mode: timingMode,
      gc_policy: timingMode === 'gc-controlled' ? 'global.gc() before warmups and samples' : 'not forced',
      gc_available: typeof global.gc === 'function',
      runs,
      warmups,
    },
  };

  for (const expected of selected) {
    const failures = [];
    const program = programs.get(expected.id);
    const validationBefore = structuralHash(context);
    const validation = runAndMaterialize(program, context);
    const contextUnchanged = validationBefore === structuralHash(context)
      && validationBefore === contextSignature;
    if (!contextUnchanged) failures.push('prepared context changed during untimed validation');
    if (!equalValues(validation.rows, expected.in_memory_rows)) {
      failures.push('in-memory rows differ from Lisp reference during validation');
    }
    for (let warmup = 0; warmup < warmups; warmup += 1) {
      if (timingMode === 'gc-controlled') global.gc();
      console.log(`[js] ${expected.id} warmup ${warmup + 1}/${warmups}`);
      const result = runAndMaterialize(program, context);
      if (!equalValues(result.rows, expected.in_memory_rows)) {
        failures.push(`warmup ${warmup + 1} result differs from Lisp reference`);
      }
    }
    const samples = [];
    for (let run = 0; run < runs; run += 1) {
      if (timingMode === 'gc-controlled') global.gc();
      console.log(`[js] ${expected.id} measured ${run + 1}/${runs}`);
      const result = runAndMaterialize(program, context);
      if (!equalValues(result.rows, expected.in_memory_rows)) {
        failures.push(`measured run ${run + 1} result differs from Lisp reference`);
      }
      if (structuralHash(context) !== contextSignature) {
        failures.push(`measured run ${run + 1} changed prepared context`);
      }
      samples.push({
        program_run_ms: result.runMs,
        materialize_ms: result.materializeMs,
        prepared_total_ms: result.preparedMs,
        elapsed_ms: result.preparedMs,
      });
    }
    const statistics = Object.fromEntries(
      ['program_run_ms', 'materialize_ms', 'prepared_total_ms']
        .map((phase) => [phase, phaseStats(samples.map((sample) => sample[phase]))]),
    );
    const passed = failures.length === 0;
    report.passed &&= passed;
    report.scenarios.push({
      id: expected.id,
      rows: Array.isArray(validation.rows) ? validation.rows.length : null,
      compile_ms: compileMs.get(expected.id),
      samples,
      statistics,
      context_unchanged: contextUnchanged,
      parity: { passed, failures },
      passed,
      failures,
    });
    console.log(`${passed ? 'PASS' : 'FAIL'} ${expected.id}: ${
      failures.length === 0 ? 'in-memory + PostgreSQL SQL + MariaDB SQL' : failures.join('; ')}`);
  }

  const output = option('--output', null);
  if (output !== null) {
    fs.writeFileSync(resolvePath(output), `${JSON.stringify(report, null, 2)}\n`);
  }
  console.log(`JS scale parity: ${report.scenarios.filter((s) => s.passed).length}/${report.scenarios.length} passed; mode=${timingMode}; runs=${runs}; warmups=${warmups}`);
  process.exitCode = report.passed ? 0 : 1;
}

correctedMain();
