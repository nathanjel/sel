// The same report with no database at all -- generated data, in memory, from JavaScript.
//
//   node examples/memory-complex/js.mjs
//
// examples/sql-complex loads the support desk from PostgreSQL. Here the same
// rows come from examples/lib/tickets-generate.sel -- a SEL program that builds
// them deterministically, and the source the PostgreSQL seed was rendered from
// -- and the same report runs over them. Nothing below opens a connection:
// the plan for MariaDB is computed from the bindings alone, and it says what it
// said for PostgreSQL, that none of this report is SQL's to answer.
//
// The four files beside this one print byte-identical output.

import { readFileSync } from 'node:fs';
import { compile, evaluate } from '../../js/src/sel.mjs';
import { Binding, planHybrid } from '../../js/src/sql/index.mjs';
import { render } from '../lib/db.mjs';

const read = (name) => readFileSync(new URL(`../lib/${name}`, import.meta.url), 'utf8');

// EXAMPLE-BEGIN generate
const data = evaluate(read('tickets-generate.sel'));
console.log('1. generated in memory');
for (const name of data.keys()) {
  console.log(`   ${name.padEnd(10)}  ${String(data.get(name).size()).padStart(3)} rows`);
}

const report = compile(read('tickets-report.sel'));
console.log('2. the report');
console.log(render(report.run(data), '   | '));
// EXAMPLE-END generate

// Planning needs the schema, not a server: the bindings sql-complex describes
// PostgreSQL with, asked about MariaDB this time.
function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

const SCHEMA = {
  TEAMS:     relation('teams', 'g', { team_id: 'NUM', team: 'TEXT' }),
  CUSTOMERS: relation('customers', 'c', { customer_id: 'NUM', customer: 'TEXT', plan: 'TEXT' }),
  SLA:       relation('sla', 's', { plan: 'TEXT', priority: 'TEXT', respond_within: 'NUM',
                                    resolve_within: 'NUM' }),
  TICKETS:   relation('tickets', 't', { ticket_id: 'NUM', customer_id: 'NUM', team_id: 'NUM',
                                        priority: 'TEXT', subject: 'TEXT', opened_at: 'NUM',
                                        closed_at: 'NUM' }),
  EVENTS:    relation('events', 'e', { event_id: 'NUM', ticket_id: 'NUM', seq: 'NUM', at: 'NUM',
                                       kind: 'TEXT', actor: 'TEXT' }),
};
const plan = planHybrid(report, 'mariadb', SCHEMA);
console.log('3. planned for MariaDB, without connecting');
console.log('   plan       ', plan.pureMemory ? 'pure_memory' : 'pushed down');
console.log('   reads      ', plan.sourceTables.join(', '));
