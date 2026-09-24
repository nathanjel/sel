// A report no database can take a share of -- SQL loads, SEL computes, from JavaScript.
//
//   tools/check-usage.sh sql-complex           (starts the databases for you)
//
// The support desk's SLA report (examples/lib/tickets-report.sel) digs incident
// numbers out of subjects with RGROUPS and searches the event log for each
// ticket's first answer. planHybrid() finds no step of it PostgreSQL can
// answer, and says so: pureMemory. So the database's job shrinks to handing
// over the tables -- with the SELECTs written by SEL too, from the same bindings
// -- and the report runs in memory over what came back. The last line checks it
// against the report over the data generated in memory (examples/memory-complex),
// which is where the rows in this database came from.
//
// The four files beside this one print byte-identical output.

import { readFileSync } from 'node:fs';
import { compile, evaluate, Value } from '../../js/src/sel.mjs';
import { Binding, Sql, planHybrid } from '../../js/src/sql/index.mjs';
import { connect, query, render } from '../lib/db.mjs';

const read = (name) => readFileSync(new URL(`../lib/${name}`, import.meta.url), 'utf8');

function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

// EXAMPLE-BEGIN load
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
const KEYS = { TEAMS: 'team_id', CUSTOMERS: 'customer_id', SLA: 'plan',
               TICKETS: 'ticket_id', EVENTS: 'event_id' };

const report = compile(read('tickets-report.sel'));
const plan = planHybrid(report, 'postgresql', SCHEMA);
console.log('1. the report, planned for PostgreSQL');
console.log('   plan       ', plan.pureMemory ? 'pure_memory' : 'pushed down');
console.log('   reads      ', plan.sourceTables.join(', '));

console.log('2. so SQL only loads the tables it reads');
const conn = await connect('postgresql');
const tables = Value.none();
for (const name of report.dependencies()) {
  const load = compile(`${name} .> SORT_BY(_["${KEYS[name]}"])`);
  const sql = Sql.translateStatement(load, 'postgresql', SCHEMA).asStatement();
  tables.set(name, await query(conn, sql));
  console.log(`   ${name.padEnd(10)}  ${String(tables.get(name).size()).padStart(3)} rows  ${sql}`);
}
await conn.close();

console.log('3. and SEL computes the report over them');
const result = report.run(tables);
console.log(render(result, '   | '));
// EXAMPLE-END load

const generated = evaluate(read('tickets-generate.sel'));
console.log('   over the generated rows:',
  report.run(generated).dump() === result.dump() ? 'same report' : 'DIFFERENT');
