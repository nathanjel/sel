# Complex data, in memory

Some questions have no share a database can take. The support desk's SLA report
below pulls incident numbers out of ticket subjects with a regular expression,
finds each ticket's first answer by searching the event log, compares it with a
target that depends on the customer's plan and the ticket's priority, and
summarises per team. The planner finds no step of it SQL can answer — so the
database's job is to hand over the tables, and SEL does the rest.

This page runs that report twice:

- **from PostgreSQL**, where SQL loads the tables and SEL computes; and
- **with no database at all**, over the same rows generated in memory by a SEL
  program.

Both print the same report.

## The data, generated

The dataset is itself a SEL program:
[`tickets-generate.sel`](../../examples/lib/tickets-generate.sel) builds teams,
agents, customers on three plans, SLA targets per plan and priority, 48 tickets
and 168 events, every "random" choice arithmetic on an id, so every host builds
the same rows. `tools/gen-usage-seed.mjs` renders exactly this into the
PostgreSQL seed, and the gate fails if the seed is stale.

<!-- from: examples/lib/tickets-generate.sel -->
```sel
# A support desk, generated: teams, agents, customers on three plans, SLA
# targets per plan and priority, tickets and the replies and notes on them.
#
# Deterministic -- every "random" choice is arithmetic on an id -- so every
# host builds the same data, and tools/gen-usage-seed.mjs renders exactly this
# into examples/sql-complex/seed.postgresql.sql. examples/memory-complex runs
# the report over this program's result; examples/sql-complex runs it over the
# same rows loaded from PostgreSQL. Times are minutes from the start of the
# quarter.
#
# The result is one record: TEAMS, AGENTS, CUSTOMERS, SLA, TICKETS, EVENTS.

N_TICKETS = 48;
IDS = SPLIT(REPEAT(",", N_TICKETS - 1), ",") .> MAP(_K);
PLANS = LIST("free", "pro", "enterprise");
PRIORITIES = LIST("low", "normal", "urgent");
TOPICS = LIST("Invoice does not match the quote", "Login loops back to the start",
              "API calls time out after deploy", "CSV export never finishes",
              "Cannot add seats to the plan", "Webhook retries flood the endpoint");

TEAMS = LIST(RECORD("team_id", 1, "team", "Billing"),
             RECORD("team_id", 2, "team", "Platform"),
             RECORD("team_id", 3, "team", "Onboarding"));

AGENTS = LIST("Ada", "Borys", "Celia", "Dmitri", "Emre", "Fatima")
  .> MAP(RECORD("agent_id", _K, "team_id", _K % 3 + 1, "agent", _));

CUSTOMERS = LIST("Acme", "Borealis", "Cobalt", "Dunmore", "Eastlake", "Fjord",
                 "Granite", "Halcyon", "Ivory", "Juniper", "Kestrel", "Lumen")
  .> MAP(RECORD("customer_id", _K, "customer", _ & " Ltd", "plan", PLANS[_K * 5 % 3 + 1]));

SLA = LIST();
ALL(PLANS, P, ALL(PRIORITIES, Q, (
  SLA[COUNT(SLA) + 1] = RECORD(
    "plan", P, "priority", Q,
    "respond_within", COND(Q $== "urgent", 30, Q $== "normal", 240, 1440)
                      / COND(P $== "enterprise", 2, 1),
    "resolve_within", COND(Q $== "urgent", 480, Q $== "normal", 2880, 7200)
                      / COND(P $== "enterprise", 2, 1));
  TRUE)));

TICKETS = IDS .> MAP(I, RECORD(
  "ticket_id", I,
  "customer_id", I * 7 % 12 + 1,
  "team_id", I * 5 % 3 + 1,
  "priority", PRIORITIES[(I * I + 3 * I) % 3 + 1],
  "subject", IF(I % 4 == 0, "[INC-" & (4000 + I * 3) & "] ", "") & TOPICS[I * 11 % 6 + 1],
  "opened_at", I * 173 % 9000,
  "closed_at", IF(I % 7 == 0, NULL, I * 173 % 9000 + (I * 89 % 40 + 1) * 60)));

EVENTS = LIST();
ALL(TICKETS, T, (
  N = T["ticket_id"];
  ALL(SPLIT(REPEAT(",", N % 4 + 1), ","), (
    SEQ = _K;
    EVENTS[COUNT(EVENTS) + 1] = RECORD(
      "event_id", COUNT(EVENTS) + 1,
      "ticket_id", N,
      "seq", SEQ,
      "at", T["opened_at"] + SEQ * (N * 37 % 300 + 5) + (N * 13 % 7) * 60 * (SEQ - 1),
      "kind", IF((N + SEQ) % 5 == 0, "note", "reply"),
      "actor", IF(SEQ % 2 == 1 AND N % 7 != 3, "agent:" & ((N + T["team_id"]) % 6 + 1), "customer"));
    TRUE));
  TRUE));

RECORD("TEAMS", TEAMS, "AGENTS", AGENTS, "CUSTOMERS", CUSTOMERS, "SLA", SLA,
       "TICKETS", TICKETS, "EVENTS", EVENTS)
```

## The report

[`tickets-report.sel`](../../examples/lib/tickets-report.sel):

<!-- from: examples/lib/tickets-report.sel -->
```sel
# The support desk's SLA report, per team: how many tickets, how many were
# answered late or not at all, how many are still open, the slowest first
# answer, and which incidents the team handled.
#
# It reads TEAMS, CUSTOMERS, SLA, TICKETS and EVENTS. examples/sql-complex runs
# it over rows loaded from PostgreSQL, examples/memory-complex over rows
# generated in memory; both print the same report. No step of it has SQL:
# the incident number is pulled out with RGROUPS, and every ticket's first
# answer is a search of the event log -- so the planner leaves it in memory
# whole, and the database's only job is to hand over the tables.

FIRST_ANSWERS = EVENTS
  .> FILTER(_["kind"] $== "reply" AND LEFT(_["actor"], 6) $== "agent:")
  .> SORT_BY(_["at"])
  .> BUCKET(_["ticket_id"], RECORD("of_ticket", _K, "at", _[1]["at"]));

TICKETS
  .> LINK(CUSTOMERS, T, C, T["customer_id"] == C["customer_id"])
  .> LINK(SLA, X, S, X["plan"] $== S["plan"] AND X["priority"] $== S["priority"])
  .> LINK_LEFT(FIRST_ANSWERS, Y, A, Y["ticket_id"] == A["of_ticket"])
  .> MAP(R, RECORD(
       "team_id", R["team_id"],
       "ticket", R["ticket_id"],
       "incident", IF(RMATCH('^\[INC-[0-9]+\]', R["subject"]),
                      RGROUPS('^\[(INC-[0-9]+)\]', R["subject"])["2"], ""),
       "answered_in", IF(IS_NULL(R["A"]["at"]), NULL, R["A"]["at"] - R["opened_at"]),
       "late", IS_NULL(R["A"]["at"]) OR R["A"]["at"] - R["opened_at"] > R["respond_within"],
       "open", IS_NULL(R["closed_at"])))
  .> BUCKET(_["team_id"], RECORD(
       "team_id", _K,
       "tickets", COUNT(_),
       "late", COUNT(FILTER(_, _["late"])),
       "open", COUNT(FILTER(_, _["open"])),
       "slowest", FILTER(_, IS_NOT_NULL(_["answered_in"]))
                    .> TOP_BY(_["answered_in"], "DESC", 1)
                    .> MAP("#" & _["ticket"] & " (" & _["answered_in"] & " min)") .> JOIN(""),
       "incidents", FILTER(_, _["incident"] $!= "") .> MAP(_["incident"]) .> JOIN(" ")))
  .> LINK(TEAMS, G, M, G["team_id"] == M["team_id"])
  .> MAP(RECORD("team", _["team"], "tickets", _["tickets"], "late", _["late"],
                "open", _["open"], "slowest", _["slowest"], "incidents", _["incidents"]))
  .> SORT_BY(_["team"])
```

A helper (`FIRST_ANSWERS`) and a pipeline with three joins, a per-row
computation, a group with nested filters and a top-1, and a final join and sort.
It reads five tables, and `dependencies()` says which.

## From PostgreSQL: SQL loads, SEL computes

The same relation bindings as every other page — and the report, planned for
PostgreSQL, comes back `pure_memory`. The application then loads the tables the
report reads, with `SELECT`s that SEL writes too, from one-step pipelines over the
same bindings, and runs the report over them:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-complex/python.py#load -->
```python
SCHEMA = {
    'TEAMS':     relation('teams', 'g', team_id='NUM', team='TEXT'),
    'CUSTOMERS': relation('customers', 'c', customer_id='NUM', customer='TEXT', plan='TEXT'),
    'SLA':       relation('sla', 's', plan='TEXT', priority='TEXT', respond_within='NUM',
                          resolve_within='NUM'),
    'TICKETS':   relation('tickets', 't', ticket_id='NUM', customer_id='NUM', team_id='NUM',
                          priority='TEXT', subject='TEXT', opened_at='NUM', closed_at='NUM'),
    'EVENTS':    relation('events', 'e', event_id='NUM', ticket_id='NUM', seq='NUM', at='NUM',
                          kind='TEXT', actor='TEXT'),
}
KEYS = {'TEAMS': 'team_id', 'CUSTOMERS': 'customer_id', 'SLA': 'plan',
        'TICKETS': 'ticket_id', 'EVENTS': 'event_id'}

report = compile(read('tickets-report.sel'))
plan = plan_hybrid(report, 'postgresql', SCHEMA)
print('1. the report, planned for PostgreSQL')
print('   plan       ', 'pure_memory' if plan.pure_memory else 'pushed down')
print('   reads      ', ', '.join(plan.source_tables))

print('2. so SQL only loads the tables it reads')
conn = connect('postgresql')
tables = Value.none()
for name in report.dependencies():
    load = compile(f'{name} .> SORT_BY(_["{KEYS[name]}"])')
    sql = Sql.translate_statement(load, 'postgresql', SCHEMA).as_statement()
    tables.set(name, query(conn, sql))
    print(f'   {name:<10}  {tables.get(name).size():>3} rows  {sql}')

print('3. and SEL computes the report over them')
result = report.run(tables)
print(render(result, '   | '))
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-complex/js.mjs#load -->
```js
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
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-complex/php.php#load -->
```php
$schema = [
    'TEAMS'     => relation('teams', 'g', ['team_id' => 'NUM', 'team' => 'TEXT']),
    'CUSTOMERS' => relation('customers', 'c', ['customer_id' => 'NUM', 'customer' => 'TEXT',
                                               'plan' => 'TEXT']),
    'SLA'       => relation('sla', 's', ['plan' => 'TEXT', 'priority' => 'TEXT',
                                         'respond_within' => 'NUM', 'resolve_within' => 'NUM']),
    'TICKETS'   => relation('tickets', 't', ['ticket_id' => 'NUM', 'customer_id' => 'NUM',
                                             'team_id' => 'NUM', 'priority' => 'TEXT',
                                             'subject' => 'TEXT', 'opened_at' => 'NUM',
                                             'closed_at' => 'NUM']),
    'EVENTS'    => relation('events', 'e', ['event_id' => 'NUM', 'ticket_id' => 'NUM',
                                            'seq' => 'NUM', 'at' => 'NUM', 'kind' => 'TEXT',
                                            'actor' => 'TEXT']),
];
$keys = ['TEAMS' => 'team_id', 'CUSTOMERS' => 'customer_id', 'SLA' => 'plan',
         'TICKETS' => 'ticket_id', 'EVENTS' => 'event_id'];

$report = Sel::compile(read('tickets-report.sel'));
$plan = Sql::planHybrid($report, 'postgresql', $schema);
echo "1. the report, planned for PostgreSQL\n";
echo '   plan        ', $plan->pureMemory ? 'pure_memory' : 'pushed down', "\n";
echo '   reads       ', implode(', ', $plan->sourceTables), "\n";

echo "2. so SQL only loads the tables it reads\n";
$conn = connect('postgresql');
$tables = Value::none();
foreach ($report->dependencies() as $name) {
    $load = Sel::compile("$name .> SORT_BY(_[\"{$keys[$name]}\"])");
    $sql = Sql::translateStatement($load, 'postgresql', $schema)->asStatement();
    $tables->set($name, query($conn, $sql));
    printf("   %-10s  %3d rows  %s\n", $name, $tables->get($name)->size(), $sql);
}

echo "3. and SEL computes the report over them\n";
$result = $report->run($tables);
echo render($result, '   | '), "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-complex/cpp.cpp#load -->
```cpp
const SqlKind NUM = SqlKind::Num, TEXT = SqlKind::Text;
const Bindings schema({
    {"TEAMS",     relation("teams", "g", {{"team_id", NUM}, {"team", TEXT}})},
    {"CUSTOMERS", relation("customers", "c", {{"customer_id", NUM}, {"customer", TEXT},
                                              {"plan", TEXT}})},
    {"SLA",       relation("sla", "s", {{"plan", TEXT}, {"priority", TEXT},
                                        {"respond_within", NUM}, {"resolve_within", NUM}})},
    {"TICKETS",   relation("tickets", "t", {{"ticket_id", NUM}, {"customer_id", NUM},
                                            {"team_id", NUM}, {"priority", TEXT},
                                            {"subject", TEXT}, {"opened_at", NUM},
                                            {"closed_at", NUM}})},
    {"EVENTS",    relation("events", "e", {{"event_id", NUM}, {"ticket_id", NUM},
                                           {"seq", NUM}, {"at", NUM}, {"kind", TEXT},
                                           {"actor", TEXT}})},
});
const std::map<std::string, std::string> keys = {
    {"TEAMS", "team_id"}, {"CUSTOMERS", "customer_id"}, {"SLA", "plan"},
    {"TICKETS", "ticket_id"}, {"EVENTS", "event_id"}};

const sel::Program report = sel::compile(read("tickets-report.sel"));
const HybridPlan plan = Sql::plan_hybrid(report, "postgresql", schema);
std::cout << "1. the report, planned for PostgreSQL\n";
std::cout << "   plan        " << (plan.pure_memory ? "pure_memory" : "pushed down") << "\n";
std::cout << "   reads       " << join(plan.source_tables, ", ") << "\n";

std::cout << "2. so SQL only loads the tables it reads\n";
const db::Connection conn = db::connect("postgresql");
sel::Value tables = sel::Value::none();
for (const std::string& name : report.dependencies()) {
  const sel::Program load = sel::compile(name + " .> SORT_BY(_[\"" + keys.at(name) + "\"])");
  const std::string sql = Sql::translate_statement(load, "postgresql", schema).as_statement();
  tables.set(name, db::query(conn, sql));
  std::cout << "   " << pad(name, 10) << "  " << rpad(std::to_string(tables.get(name)->size()), 3)
            << " rows  " << sql << "\n";
}

std::cout << "3. and SEL computes the report over them\n";
const sel::Value result = report.run(tables);
std::cout << db::render(result, "   | ") << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-complex/lisp.lisp#load -->
```lisp
(defparameter *schema*
  (list (cons "TEAMS"     (relation "teams" "g" "team_id" :num "team" :text))
        (cons "CUSTOMERS" (relation "customers" "c" "customer_id" :num
                                    "customer" :text "plan" :text))
        (cons "SLA"       (relation "sla" "s" "plan" :text "priority" :text
                                    "respond_within" :num "resolve_within" :num))
        (cons "TICKETS"   (relation "tickets" "t" "ticket_id" :num "customer_id" :num
                                    "team_id" :num "priority" :text "subject" :text
                                    "opened_at" :num "closed_at" :num))
        (cons "EVENTS"    (relation "events" "e" "event_id" :num "ticket_id" :num
                                    "seq" :num "at" :num "kind" :text "actor" :text))))

(defparameter *keys*
  '(("TEAMS" . "team_id") ("CUSTOMERS" . "customer_id") ("SLA" . "plan")
    ("TICKETS" . "ticket_id") ("EVENTS" . "event_id")))

(defun report-from-database ()
  "Plan the report, load what it reads from PostgreSQL, and run it in memory.
Returns the result and the compiled report."
  (let* ((report (sel:compile-source (read-lib "tickets-report.sel")))
         (plan (sel.sql:plan-hybrid report "postgresql" *schema*)))
    (format t "1. the report, planned for PostgreSQL~%")
    (format t "   plan        ~a~%"
            (if (sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory" "pushed down"))
    (format t "   reads       ~{~a~^, ~}~%" (sel.sql:hybrid-plan-source-tables plan))

    (format t "2. so SQL only loads the tables it reads~%")
    (let ((conn (sel-db:connect "postgresql"))
          (tables (sel:make-none)))
      (dolist (name (sel:dependencies report))
        (let* ((loader (sel:compile-source
                        (format nil "~a .> SORT_BY(_[\"~a\"])"
                                name (cdr (assoc name *keys* :test #'string=)))))
               (sql (sel.sql:as-statement
                     (sel.sql:translate-statement loader "postgresql" *schema*))))
          (sel:value-set tables name (sel-db:query conn sql))
          (format t "   ~10a  ~3D rows  ~a~%"
                  name (sel:value-size (sel:value-get tables name)) sql)))

      (format t "3. and SEL computes the report over them~%")
      (let ((result (sel:run report tables)))
        (format t "~a~%" (sel-db:render result "   | "))
        (values result report)))))
```

</details>
<!-- /tabs -->

<!-- from: examples/sql-complex/output.txt -->
```text
1. the report, planned for PostgreSQL
   plan        pure_memory
   reads       events, tickets, customers, sla, teams
2. so SQL only loads the tables it reads
   CUSTOMERS    12 rows  SELECT "c".* FROM "customers" "c" ORDER BY "c"."customer_id" ASC
   EVENTS      168 rows  SELECT "e".* FROM "events" "e" ORDER BY "e"."event_id" ASC
   SLA           9 rows  SELECT "s".* FROM "sla" "s" ORDER BY CAST("s"."plan" AS TEXT) COLLATE "C" ASC
   TEAMS         3 rows  SELECT "g".* FROM "teams" "g" ORDER BY "g"."team_id" ASC
   TICKETS      48 rows  SELECT "t".* FROM "tickets" "t" ORDER BY "t"."ticket_id" ASC
3. and SEL computes the report over them
   | team=Billing  tickets=16  late=4  open=2  slowest=#39 (1104 min)  incidents=INC-4036 INC-4072 INC-4108 INC-4144
   | team=Onboarding  tickets=16  late=8  open=2  slowest=#19 (564 min)  incidents=INC-4012 INC-4048 INC-4084 INC-4120
   | team=Platform  tickets=16  late=9  open=2  slowest=#29 (1254 min)  incidents=INC-4024 INC-4060 INC-4096 INC-4132
   over the generated rows: same report
```

The last line runs the report once more over the generated data and compares:
the rows that went through PostgreSQL and the rows that never left memory give
the same report.

## Without a database: generated, in memory

The same report over the generated rows, with nothing to connect to. The plan is
still available — planning needs the schema, not a server — and asked about
**MariaDB** this time, it agrees:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/memory-complex/python.py#generate -->
```python
data = evaluate(read('tickets-generate.sel'))
print('1. generated in memory')
for name in data.keys():
    print(f'   {name:<10}  {data.get(name).size():>3} rows')

report = compile(read('tickets-report.sel'))
print('2. the report')
print(render(report.run(data), '   | '))
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/memory-complex/js.mjs#generate -->
```js
const data = evaluate(read('tickets-generate.sel'));
console.log('1. generated in memory');
for (const name of data.keys()) {
  console.log(`   ${name.padEnd(10)}  ${String(data.get(name).size()).padStart(3)} rows`);
}

const report = compile(read('tickets-report.sel'));
console.log('2. the report');
console.log(render(report.run(data), '   | '));
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/memory-complex/php.php#generate -->
```php
$data = Sel::evaluate(read('tickets-generate.sel'));
echo "1. generated in memory\n";
foreach ($data->keys() as $name) {
    printf("   %-10s  %3d rows\n", $name, $data->get($name)->size());
}

$report = Sel::compile(read('tickets-report.sel'));
echo "2. the report\n";
echo render($report->run($data), '   | '), "\n";
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/memory-complex/cpp.cpp#generate -->
```cpp
sel::Value data = sel::evaluate(read("tickets-generate.sel"));
std::cout << "1. generated in memory\n";
for (const std::string& name : data.keys())
  std::cout << "   " << pad(name, 10) << "  " << rpad(std::to_string(data.get(name)->size()), 3)
            << " rows\n";

const sel::Program report = sel::compile(read("tickets-report.sel"));
std::cout << "2. the report\n";
std::cout << db::render(report.run(data), "   | ") << "\n";
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/memory-complex/lisp.lisp#generate -->
```lisp
(defun report-in-memory ()
  "Generate the support desk in memory, report on it, and return the report."
  (let ((data (sel:evaluate (read-lib "tickets-generate.sel")))
        (report (sel:compile-source (read-lib "tickets-report.sel"))))
    (format t "1. generated in memory~%")
    (dolist (name (sel:value-keys data))
      (format t "   ~10a  ~3D rows~%" name (sel:value-size (sel:value-get data name))))
    (format t "2. the report~%")
    (format t "~a~%" (sel-db:render (sel:run report data) "   | "))
    report))
```

</details>
<!-- /tabs -->

<!-- from: examples/memory-complex/output.txt -->
```text
1. generated in memory
   TEAMS         3 rows
   AGENTS        6 rows
   CUSTOMERS    12 rows
   SLA           9 rows
   TICKETS      48 rows
   EVENTS      168 rows
2. the report
   | team=Billing  tickets=16  late=4  open=2  slowest=#39 (1104 min)  incidents=INC-4036 INC-4072 INC-4108 INC-4144
   | team=Onboarding  tickets=16  late=8  open=2  slowest=#19 (564 min)  incidents=INC-4012 INC-4048 INC-4084 INC-4120
   | team=Platform  tickets=16  late=9  open=2  slowest=#29 (1254 min)  incidents=INC-4024 INC-4060 INC-4096 INC-4132
3. planned for MariaDB, without connecting
   plan        pure_memory
   reads       events, tickets, customers, sla, teams
```

This is also how such a report is tested: the generator is the fixture, the
report is the code under test, and the database is one more way to deliver the
same rows.
