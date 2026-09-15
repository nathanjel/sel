# L. GROUP BY over a TEXT key is emitted without the binary collation applied to $==

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Grouped and sorted TEXT keys are collated".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** js, php, python, cpp, lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

All five translators render a TEXT bucket key in GROUP BY (and in the `_K` projection) bare, without the textCast+textCollate wrapping the same translators apply to the `$` family, and I reproduced the consequence on live servers: for rows cat = 'A','a','A ' the evaluator in every host answers three groups, MariaDB 11.8 (utf8mb4_uca1400_ai_ci) answers one group (A:3) and MySQL 8.4 (utf8mb4_0900_ai_ci) answers two (A:2, 'A ':1) for the SQL the translator emits as a pure_sql plan. The hosts agree byte-for-byte on the emitted SQL (stmt.bucket.basic passes in all five), so this is an in-memory-vs-SQL lane divergence, not a host divergence; it is invisible to the suite because the string cases only pin bytes, the Python unit test executes against SQLite (BINARY), and sql/oracle covers expressions only, not statements. The bare rendering predates the commit (present at 8fe0e3a, introduced with GROUP BY in cd06049); the commit widened its reach by folding BUCKET .> MAP into one pure_sql grouped statement and by documenting bucket/evaluator parity.

## Suggested fix — case first

In each host's statement renderer, wrap every GROUP BY key whose inferred kind is TEXT with the dialect's textCast+textCollate (the same helper the `$` family uses), and render the `_K` select-list projection with the identical wrapped expression so MySQL's only_full_group_by accepts it (verified: bare `cat` + collated GROUP BY is ER_1055 on MySQL 8.4; matching collated expressions work on both). Do the same for TEXT ORDER BY keys and DISTINCT, which share the class. Add first: a .sqlt case (e.g. stmt.bucket.text-key-is-collated, dialect mariadb) pinning `GROUP BY CAST(`dept` AS CHAR) COLLATE utf8mb4_bin` and its projection, plus a mirrored postgresql/sqlite expectation; then extend the DB-backed lane (sql/oracle or the Python SQLite unit test's MariaDB/MySQL variant via tools/oracle-db.sh) with rows ('A'),('a') so the count of groups is compared against run(). Document separately that utf8mb4_bin is PAD SPACE on MariaDB/MySQL, so 'A' vs 'A ' still merge (also under today's `$==`) — either a NO PAD collation choice or a new caveat entry.

## Verifier reasoning

Reading: js/src/sql/translator.mjs:2377-2387, php/src/Sql/Translator.php:3107-3120, python/sel/sql/translator.py:2118-2124, cpp/sel_sql_translator.cpp:2885-2897, lisp/src/sql/translator.lisp:2264-2275 all emit ' GROUP BY ' followed by this.node(gb.node) with no cast/collation; the select-list `_K` is rendered the same way (probe output: SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat`). The evaluator's bucket compares keys with Value.eql (js/src/builtins/aggregate.mjs:431 `group.key.eql(groupKey)`), byte-exact for text, and all five REPLs give three groups for the repro. The docs promise: docs/SQL-TRANSLATION.md:238-241 explains textCollate exists precisely because MySQL's default collation is case/accent-insensitive; §11.2 (line 1911) says "What survives here is the same divergence with a column in it, and nothing else"; the `text-collation` caveat (MAP.md:537, SQL-TRANSLATION.md:1867) is about a *declared* column collation defeating textCollate, not about a clause that never applies it; CHANGELOG [Unreleased] states BUCKET(k) .> MAP(proj) "translates as one grouped statement in every host — it is BUCKET(k, proj) in the evaluator" and EXTENDING.md:600-601 says a prefix is a split point only if its SQL rows are the value the evaluator would have produced. Under the rubric, differing results between lanes = high, even though the original reporter rated it medium. Pre-existing: `git show 8fe0e3a:js/src/sql/translator.mjs` lines 2344-2354 already contain the identical bare GROUP BY loop. Same class, out of the cluster's stated scope but verified: ORDER BY on a TEXT key (SORT_BY) orders A, a, 'A ' on MariaDB/MySQL and a, A, 'A ' on PostgreSQL 17 vs A, 'A ', a in run(); and utf8mb4_bin is PAD SPACE on both MySQL-family servers, so 'A' vs 'A ' merge even under textCollate (and `"A" $== "A "` renders true on both servers today while SEL says FALSE) — a separate pre-existing gap the fix cannot close by collation alone. On MySQL 8.4 a naive `GROUP BY cat COLLATE utf8mb4_bin` with a bare `cat` in the select list is ER_1055 (only_full_group_by); projecting the same collated expression works on both servers.

## Verifier evidence

```
Probe /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-L/probe.mjs (JS translator, Binding.relation('items', {CAT: TEXT, V: NUM}), rows A/a/'A '):
  ITEMS .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_)))
  run():  {"1":{"cat":"A","n":"1"},"2":{"cat":"a","n":"1"},"3":{"cat":"A ","n":"1"}}
  mariadb pure_sql SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat`
  mysql   pure_sql (same)   postgresql/sqlite pure_sql SELECT "cat" AS "cat", COUNT(*) AS "n" FROM "items" GROUP BY "cat"
  ITEMS .> FILTER(_["cat"] $== "a")  -> mariadb: WHERE (CAST(`cat` AS CHAR) COLLATE utf8mb4_bin = CAST('a' AS CHAR) COLLATE utf8mb4_bin)
Live servers (docker mariadb:11.8, mysql:8.4, postgres:17, torn down afterwards), table items(cat VARCHAR(16), v INT) rows ('A',1),('a',2),('A ',3), file verify-L/run.sql:
  MariaDB 11.8.8 (@@collation_database utf8mb4_uca1400_ai_ci): SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat` -> A 3 (one group)
  MySQL 8.4 (utf8mb4_0900_ai_ci): same statement -> A 2 / 'A ' 1 (two groups)
  PostgreSQL 17: -> A 1 / a 1 / 'A ' 1 (agrees)
  MariaDB: SELECT `cat` AS `cat`, COUNT(*) FROM items GROUP BY CAST(`cat` AS CHAR) COLLATE utf8mb4_bin -> A 2 / a 1 (PAD SPACE still merges 'A ' with 'A'); SELECT CAST('A' AS CHAR) COLLATE utf8mb4_bin = CAST('A ' AS CHAR) COLLATE utf8mb4_bin -> 1 on both MariaDB and MySQL
  MySQL: GROUP BY `cat` COLLATE utf8mb4_bin with bare `cat` in select list -> ERROR 1055 only_full_group_by; SELECT CAST(`cat` AS CHAR) COLLATE utf8mb4_bin AS `cat`, COUNT(*) ... GROUP BY CAST(`cat` AS CHAR) COLLATE utf8mb4_bin -> A 2 / a 1
  ORDER BY `cat` ASC: MariaDB/MySQL A, a, 'A '; PostgreSQL a, A, 'A '; run(): A, 'A ', a
Evaluator lane, all five hosts (node js/bin/sel.mjs / php php/bin/sel / cpp/build/sel / lisp/bin/sel / python3 -m sel -e 'LIST(RECORD("cat","A","v",1), RECORD("cat","a","v",2), RECORD("cat","A ","v",3)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_)))'): identical output -{"1"=-{"cat"=t"A","n"=t"1"}, "2"=-{"cat"=t"a","n"=t"1"}, "3"=-{"cat"=t"A ","n"=t"1"}}
Case runner stmt.bucket.basic (expects SELECT `dept` FROM `items` GROUP BY `dept`): 1 passed in js, php, cpp, lisp, python.
Source: js/src/sql/translator.mjs:2377-2387; php/src/Sql/Translator.php:3107-3120; python/sel/sql/translator.py:2118-2124; cpp/sel_sql_translator.cpp:2885-2897; lisp/src/sql/translator.lisp:2264-2275; js/src/builtins/aggregate.mjs:431 (eql key compare); git show 8fe0e3a:js/src/sql/translator.mjs lines 2344-2354 (same bare loop before the commit); docs/SQL-TRANSLATION.md:238-241, 1911, 1867; sql/MAP.md:537; docs/EXTENDING.md:600-601; sql/oracle/README.md has no statement/GROUP BY coverage (grep hit only "native prepared statement").
```

## Original review reports (deduplicated into this finding)

### [bucket-translator] GROUP BY over a TEXT key is emitted without the binary collation the same translator applies to $==, so MariaDB/MySQL merge groups the evaluator keeps apart

*doc-claim · medium · hosts: js, php, python, cpp, lisp*

Locations: `js/src/sql/translator.mjs:2379`; `php/src/Sql/Translator.php:3109`; `python/sel/sql/translator.py:2119`; `cpp/sel_sql_translator.cpp:2887`; `lisp/src/sql/translator.lisp:2266`; `docs/SQL-TRANSLATION.md:238`

The evaluator's bucket keys are compared with Value.eql — byte-exact for text, so 'A' and 'a' (or 'A' and 'A ') are two groups in every host. The rendered GROUP BY `dept` carries no COLLATE, and docs/SQL-TRANSLATION.md:238 itself records that MySQL's default collation is case- and accent-insensitive (with PAD SPACE), which is why every $-comparison gets textCollate. On MariaDB/MySQL the bucket fold therefore yields fewer groups than run(), and the _K projected for a merged group is whichever spelling the engine picks; the fixture files, the mirror check and the SQLite unit test cannot see it because SQLite's default is BINARY. Not verified against a live MariaDB here (no DSN); the claim rests on the docs' own description of the default collation.

Reported repro:

```
REPL, all five hosts: LIST(RECORD("cat","A","v",1), RECORD("cat","a","v",2)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_))) => two rows (A:1, a:1). sql/cases/24-bucket.sqlt stmt.bucket.basic renders `SELECT `dept` FROM `items` GROUP BY `dept`` — bare column, versus stmt.bucket.refusal-having-bool's neighbours where `$==` on the same column renders `CAST(`dept` AS CHAR) COLLATE utf8mb4_bin`. tools/check-sql-oracle.sh with a MariaDB DSN over rows ('A'),('a') would return one group.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$SCRATCH/verify-L/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';

const items = { ITEMS: Binding.relation('items', null, {
  CAT: Binding.column('cat', null, 'TEXT'),
  V: Binding.column('v', null, 'NUM'),
}) };
const rows = [{ cat: 'A', v: 1 }, { cat: 'a', v: 2 }, { cat: 'A ', v: 3 }];
const programs = [
  'ITEMS .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_)))',
  'ITEMS .> BUCKET(_["cat"])',
  'ITEMS .> DISTINCT()',
  'ITEMS .> SORT_BY(_["cat"], "ASC") .> MAP(_["cat"])',
  'ITEMS .> FILTER(_["cat"] $== "a")',
];
for (const src of programs) {
  const p = compile(src);
  console.log('### ' + src);
  console.log('run():  ' + JSON.stringify(p.run({ ITEMS: rows }).toNative()));
  for (const d of ['mariadb', 'mysql', 'postgresql', 'sqlite']) {
    const plan = Sql.planHybrid(p, d, items);
    const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
    const sql = plan.sqlStatement ? plan.sqlStatement.asStatement('inline') : '(none)';
    console.log(`${d.padEnd(10)} ${kind.padEnd(11)} ${sql}`);
  }
}
```

**`run.sql`**

```sql
DROP TABLE IF EXISTS items;
CREATE TABLE items (cat VARCHAR(16), v INT);
INSERT INTO items VALUES ('A', 1), ('a', 2), ('A ', 3);
SELECT '--- bucket+map' AS q;
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat`;
SELECT '--- bare bucket' AS q;
SELECT `cat` FROM `items` GROUP BY `cat`;
SELECT '--- distinct' AS q;
SELECT DISTINCT * FROM `items`;
SELECT '--- sort_by' AS q;
SELECT `_sub1`.`cat` FROM (SELECT * FROM `items` ORDER BY `cat` ASC) `_sub1`;
SELECT '--- $== (collated)' AS q;
SELECT * FROM `items` WHERE (CAST(`cat` AS CHAR) COLLATE utf8mb4_bin = CAST('a' AS CHAR) COLLATE utf8mb4_bin);
SELECT '--- what GROUP BY with the collation would give' AS q;
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS CHAR) COLLATE utf8mb4_bin;
```

### Commands run and their output

Check DB availability and oracle-db script

```bash
which docker podman mysql mariadb psql sqlite3 2>&1; docker ps 2>&1 | head -3; sed -n 1,60p tools/oracle-db.sh
```

```
/usr/bin/which: no podman in (/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.local/bin:/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.cargo/bin:/home/nathan/.nvm/versions/node/v22.12.0/bin:/home/nathan/.local/bin:/home/nathan/bin:/usr/lib64/ccache:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/usr/lib/wsl/lib:/mnt/c/Windows/system32:/mnt/c/Windows:/mnt/c/Windows/System32/Wbem:/mnt/c/Windows/System32/WindowsPowerShell/v1.0/:/mnt/c/Windows/System32/OpenSSH/:/mnt/c/Program Files/dotnet/:/mnt/c/Program Files (x86)/NVIDIA Corporation/PhysX/Common:/mnt/c/Program Files/Antelope Audio/Antelope Launcher/:/mnt/c/Program Files/Git/cmd:/mnt/c/Program Files/CMake/bin:/mnt/c/Program Files (x86)/Windows Kits/10/Windows Performance Toolkit/:/mnt/c/Program Files/WSL/:/mnt/c/Users/marci/AppData/Local/agy/bin:/mnt/c/Users/marci/.local/bin:/mnt/c/Users/marci/AppData/Local/Microsoft/WindowsApps:/mnt/c/Users/marci/AppData/Local/Programs/Microsoft VS Code/bin:/mnt/c/Users/marci/AppData/Local/nvm:/mnt/c/nvm4w/nodejs:/mnt/c/Users/marci/AppData/Local/Microsoft/WinGet/Links:/home/nathan/.composer/vendor/bin)
/usr/bin/which: no sqlite3 in (/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.local/bin:/home/nathan/.opencode/bin:/home/nathan/.local/bin:/home/nathan/.kimi-code/bin:/home/nathan/.cargo/bin:/home/nathan/.nvm/versions/node/v22.12.0/bin:/home/nathan/.local/bin:/home/nathan/bin:/usr/lib64/ccache:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/usr/lib/wsl/lib:/mnt/c/Windows/system32:/mnt/c/Windows:/mnt/c/Windows/System32/Wbem:/mnt/c/Windows/System32/WindowsPowerShell/v1.0/:/mnt/c/Windows/System32/OpenSSH/:/mnt/c/Program Files/dotnet/:/mnt/c/Program Files (x86)/NVIDIA Corporation/PhysX/Common:/mnt/c/Program Files/Antelope Audio/Antelope Launcher/:/mnt/c/Program Files/Git/cmd:/mnt/c/Program Files/CMake/bin:/mnt/c/Program Files (x86)/Windows Kits/10/Windows Performance Toolkit/:/mnt/c/Program Files/WSL/:/mnt/c/Users/marci/AppData/Local/agy/bin:/mnt/c/Users/marci/.local/bin:/mnt/c/Users/marci/AppData/Local/Microsoft/WindowsApps:/mnt/c/Users/marci/AppData/Local/Programs/Microsoft VS Code/bin:/mnt/c/Users/marci/AppData/Local/nvm:/mnt/c/nvm4w/nodejs:/mnt/c/Users/marci/AppData/Local/Microsoft/WinGet/Links:/home/nathan/.composer/vendor/bin)
/usr/sbin/docker
/usr/sbin/mysql
/usr/sbin/mariadb
/usr/sbin/psql
CONTAINER ID   IMAGE                                COMMAND                  CREATED        STATUS                 PORTS                                           NAMES
55dfb4e86809   hashicorp/vault:latest               "/bin/sh /usr/local/…"   2 hours ago    Up 2 hours (healthy)   0.0.0.0:8200->8200/tcp, [::]:8200->8200/tcp     vault
ff854e35573e   postgres:17-bookworm                 "docker-entrypoint.s…"   3 hours ago    Up 3 hours (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp     int4cloud-db
#!/usr/bin/env bash
# Start a throwaway database per dialect, run the semantic oracle against it,
# and take it down again.
#
#   tools/oracle-db.sh                 up, run the oracle, down (the usual)
#   tools/oracle-db.sh run <cmd>...    up, run <cmd> with the DSNs exported, down
#   tools/oracle-db.sh up              start and print the exports, leave running
#   tools/oracle-db.sh down            stop and remove whatever this run started
#
# 
… (truncated)
```

Start a throwaway MariaDB 11.8 container

```bash
docker images | grep -i "mariadb\|mysql" ; docker run -d --rm --name sel-verify-L -e MARIADB_ROOT_PASSWORD=root -e MARIADB_DATABASE=sel -p 127.0.0.1:33068:3306 --tmpfs /var/lib/mysql mariadb:11.8 2>&1 | tail -2
```

```
bench/client-php-mariadb:latest                                                                          0003ec55d52b        755MB          185MB        
mariadb:10.5                                                                                             a530aeeefd82        543MB          126MB        
mariadb:11.8                                                                                             efb4959ef2c8        470MB          110MB        
mysql:8.4                                                                                                b3b90af2a655       1.12GB          255MB   U    
9766181626bd9a5a54016685188f96957e48e4db0b272678c581a9393da365af
```

Find JS SQL API usage examples

```bash
mkdir -p $SCRATCH/verify-L && cd /home/nathan/workspaces/nth-share/sel && sed -n 1,80p tools/check-js-optimizer.mjs | grep -n "import\|Binding\|Sql\.\|translate\|plan" | head -30; grep -n "translateStatement\|translate_statement\|static translate\|  translate(" js/src/sql/index.mjs js/src/sql/*.mjs | head
```

```
5:import assert from 'node:assert/strict';
6:import '../js/src/builtins/index.mjs';
7:import { parse } from '../js/src/parser.mjs';
8:import {
11:import { compile } from '../js/src/sel.mjs';
12:import { Binding, Sql } from '../js/src/sql/index.mjs';
js/src/sql/index.mjs:32:  static translate(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:53:  static translateStatement(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:55:    return t.translateStatement(program.ast);
js/src/sql/index.mjs:61:      return Sql.translateStatement(program, dialect, bindings, options);
js/src/sql/index.mjs:32:  static translate(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:53:  static translateStatement(program, dialect, bindings = null, options = null) {
js/src/sql/index.mjs:55:    return t.translateStatement(program.ast);
js/src/sql/index.mjs:61:      return Sql.translateStatement(program, dialect, bindings, options);
js/src/sql/hybrid.mjs:68:    return translator.translateStatement(ast);
js/src/sql/translator.mjs:139:  translate(ast) {
```

Render SQL per dialect and run the evaluator lane for the probe programs

```bash
# (file written, see "Reproduction scripts" above)
node $SCRATCH/verify-L/probe.mjs
```

```
### ITEMS .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_)))
run():  {"1":{"cat":"A","n":"1"},"2":{"cat":"a","n":"1"},"3":{"cat":"A ","n":"1"}}
mariadb    pure_sql    SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat`
mysql      pure_sql    SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat`
postgresql pure_sql    SELECT "cat" AS "cat", COUNT(*) AS "n" FROM "items" GROUP BY "cat"
sqlite     pure_sql    SELECT "cat" AS "cat", COUNT(*) AS "n" FROM "items" GROUP BY "cat"
### ITEMS .> BUCKET(_["cat"])
run():  {"A":{"1":{"cat":"A","v":"1"}},"a":{"1":{"cat":"a","v":"2"}},"A ":{"1":{"cat":"A ","v":"3"}}}
mariadb    pure_sql    SELECT `cat` FROM `items` GROUP BY `cat`
mysql      pure_sql    SELECT `cat` FROM `items` GROUP BY `cat`
postgresql pure_sql    SELECT "cat" FROM "items" GROUP BY "cat"
sqlite     pure_sql    SELECT "cat" FROM "items" GROUP BY "cat"
### ITEMS .> DISTINCT()
run():  {"1":{"cat":"A","v":"1"},"2":{"cat":"a","v":"2"},"3":{"cat":"A ","v":"3"}}
mariadb    pure_sql    SELECT DISTINCT * FROM `items`
mysql      pure_sql    SELECT DISTINCT * FROM `items`
postgresql pure_sql    SELECT DISTINCT * FROM "items"
sqlite     pure_sql    SELECT DISTINCT * FROM "items"
### ITEMS .> SORT_BY(_["cat"], "ASC") .> MAP(_["cat"])
run():  {"1":"A","2":"A ","3":"a"}
mariadb    pure_sql    SELECT `_sub1`.`cat` FROM (SELECT * FROM `items` ORDER BY `cat` ASC) `_sub1`
mysql      pure_sql    SELECT `_sub1`.`cat` FROM (SELECT * FROM `items` ORDER BY `cat` ASC) `_sub1`
postgresql pure_sql    SELECT "_sub1"."cat" FROM (SELECT * FROM "items" ORDER BY "cat" ASC) "_sub1"
sqlite     pure_sql    SELECT "_sub1"."cat" FROM (SELECT * FROM "items" ORDER BY "cat" ASC) "_sub1"
### ITEMS .> FILTER(_["cat"] $== "a")
run():  {"2":{"cat":"a","v":"2"}}
mariadb    pure_sql    SELECT * FROM `items` WHERE (CAST(`cat` AS CHAR) COLLATE utf8mb4_bin = CAST('a' AS CHAR) COLLATE utf8mb4_bin)
mysql      pure_sql    SELECT * FROM `items` WHERE (CAST(`cat` AS CHAR) COLLATE utf8mb4_bin = CAST('a' AS CHAR) COLLATE utf8mb4_bin)
postgresql pure_sql    SELECT * FROM "items" WHERE (CAST("cat" AS TEXT) COLLATE "C" = CAST('a' AS TEXT) COLLATE "C")
sqlite     pure_sql    SELECT * FROM "items" WHERE (CAST("cat" AS TEXT) = CAST('a' AS TEXT))
```

Wait for MariaDB and print version and default collations

```bash
for i in $(seq 1 40); do mariadb -h127.0.0.1 -P33068 -uroot -proot -e 'select 1' >/dev/null 2>&1 && break; sleep 2; done; mariadb -h127.0.0.1 -P33068 -uroot -proot sel -e "SELECT VERSION(); SELECT @@collation_database, @@character_set_connection, @@collation_connection;"
```

```
VERSION()
11.8.8-MariaDB-ubu2404
@@collation_database	@@character_set_connection	@@collation_connection
utf8mb4_uca1400_ai_ci	utf8mb4	utf8mb4_uca1400_ai_ci
```

Execute the rendered statements against MariaDB 11.8 with rows A, a, 'A '

```bash
cd $SCRATCH/verify-L && # (file written, see "Reproduction scripts" above)
mariadb -h127.0.0.1 -P33068 -uroot -proot sel < run.sql
```

```
q
--- bucket+map
cat	n
A	3
q
--- bare bucket
cat
A
q
--- distinct
cat	v
A	1
a	2
A 	3
q
--- sort_by
cat
A
a
A 
q
--- $== (collated)
cat	v
a	2
q
--- what GROUP BY with the collation would give
cat	n
A	2
a	1
```

Check PAD SPACE under utf8mb4_bin on MariaDB and rerun the statements on MySQL 8.4

```bash
mariadb -h127.0.0.1 -P33068 -uroot -proot sel -e "SELECT CAST('A' AS CHAR) COLLATE utf8mb4_bin = CAST('A ' AS CHAR) COLLATE utf8mb4_bin AS pad_eq, 'A' = 'a' AS ci_eq;"; docker run -d --rm --name sel-verify-L-mysql -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=sel -p 127.0.0.1:33069:3306 --tmpfs /var/lib/mysql mysql:8.4 >/dev/null && for i in $(seq 1 60); do mysql -h127.0.0.1 -P33069 -uroot -proot -e 'select 1' >/dev/null 2>&1 && break; sleep 2; done; mysql -h127.0.0.1 -P33069 -uroot -proot sel < $SCRATCH/verify-L/run.sql 2>&1 | grep -v "Using a password"
```

```
pad_eq	ci_eq
1	1
mysql: Deprecated program name. It will be removed in a future release, use '/usr/bin/mariadb' instead
q
--- bucket+map
cat	n
A	2
A 	1
q
--- bare bucket
cat
A
A 
q
--- distinct
cat	v
A	1
a	2
A 	3
q
--- sort_by
cat
A
a
A 
q
--- $== (collated)
cat	v
a	2
q
--- what GROUP BY with the collation would give
--------------
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS CHAR) COLLATE utf8mb4_bin
--------------

ERROR 1055 (42000) at line 15: Expression #1 of SELECT list is not in GROUP BY clause and contains nonaggregated column 'sel.items.cat' which is not functionally dependent on columns in GROUP BY clause; this is incompatible with sql_mode=only_full_group_by
```

Try candidate collated GROUP BY spellings on both servers

```bash
mysql -h127.0.0.1 -P33069 -uroot -proot sel 2>/dev/null <<'EOF'
SELECT '--- GROUP BY cat COLLATE utf8mb4_bin' AS q;
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat` COLLATE utf8mb4_bin;
SELECT '--- GROUP BY CAST(cat AS BINARY)' AS q;
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS BINARY);
SELECT '--- projected collated expr' AS q;
SELECT CAST(`cat` AS CHAR) COLLATE utf8mb4_bin AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS CHAR) COLLATE utf8mb4_bin;
EOF
echo ==== mariadb; mariadb -h127.0.0.1 -P33068 -uroot -proot sel <<'EOF'
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat` COLLATE utf8mb4_bin;
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS BINARY);
EOF
```

```
q
--- GROUP BY cat COLLATE utf8mb4_bin
==== mariadb
cat	n
A	2
a	1
cat	n
A	1
A 	1
a	1
```

Run each candidate spelling separately on MySQL 8.4

```bash
mysql -h127.0.0.1 -P33069 -uroot -proot sel 2>&1 <<'EOF' | grep -v "password\|Deprecated"
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat` COLLATE utf8mb4_bin;
EOF
mysql -h127.0.0.1 -P33069 -uroot -proot sel 2>&1 <<'EOF' | grep -v "password\|Deprecated"
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS BINARY);
EOF
mysql -h127.0.0.1 -P33069 -uroot -proot sel 2>&1 <<'EOF' | grep -v "password\|Deprecated"
SELECT CAST(`cat` AS CHAR) COLLATE utf8mb4_bin AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS CHAR) COLLATE utf8mb4_bin;
EOF
```

```
--------------
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY `cat` COLLATE utf8mb4_bin
--------------

ERROR 1055 (42000) at line 1: Expression #1 of SELECT list is not in GROUP BY clause and contains nonaggregated column 'sel.items.cat' which is not functionally dependent on columns in GROUP BY clause; this is incompatible with sql_mode=only_full_group_by
--------------
SELECT `cat` AS `cat`, COUNT(*) AS `n` FROM `items` GROUP BY CAST(`cat` AS BINARY)
--------------

ERROR 1055 (42000) at line 1: Expression #1 of SELECT list is not in GROUP BY clause and contains nonaggregated column 'sel.items.cat' which is not functionally dependent on columns in GROUP BY clause; this is incompatible with sql_mode=only_full_group_by
cat	n
A	2
a	1
```

Look at the PHP sqlt suite-error lines

```bash
php php/bin/sqlt stmt.bucket.basic 2>&1 | grep -v "^$" | head -8
```

```
UNPINNED ansi-probe ops.& carries the caveat `concat-null`, so the oracle may not fail on it, and no case pins the string it emits; add one
UNPINNED ansi-probe ops./ carries the caveat `division-scale`, so the oracle may not fail on it, and no case pins the string it emits; add one
UNPINNED ansi-probe funcs.LOWER carries the caveat `unicode-case`, so the oracle may not fail on it, and no case pins the string it emits; add one
UNPINNED ansi-probe funcs.LTRIM carries the caveat `trim-charset`, so the oracle may not fail on it, and no case pins the string it emits; add one
UNPINNED ansi-probe funcs.POWER carries the caveat `power-float`, so the oracle may not fail on it, and no case pins the string it emits; add one
UNPINNED ansi-probe funcs.RTRIM carries the caveat `trim-charset`, so the oracle may not fail on it, and no case pins the string it emits; add one
UNPINNED ansi-probe funcs.TRIM carries the caveat `trim-charset`, so the oracle may not fail on it, and no case pins the string it emits; add one
UNPINNED ansi-probe funcs.UPPER carries the caveat `unicode-case`, so the oracle may not fail on it, and no case pins the string it emits; add one
```

Run the evaluator-lane repro in all five hosts

```bash
E='LIST(RECORD("cat","A","v",1), RECORD("cat","a","v",2), RECORD("cat","A ","v",3)) .> BUCKET(_["cat"]) .> MAP(RECORD("cat", _K, "n", COUNT(_)))'; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel" "env PYTHONPATH=$PWD/python python3 -m sel"; do echo "== $h"; $h -e "$E" 2>&1 | tail -3; done
```

```
== node js/bin/sel.mjs
-{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"a", "n"=t"1"}, "3"=-{"cat"=t"A ", "n"=t"1"}}
== php php/bin/sel
-{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"a", "n"=t"1"}, "3"=-{"cat"=t"A ", "n"=t"1"}}
== cpp/build/sel
-{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"a", "n"=t"1"}, "3"=-{"cat"=t"A ", "n"=t"1"}}
== lisp/bin/sel
-{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"a", "n"=t"1"}, "3"=-{"cat"=t"A ", "n"=t"1"}}
== env PYTHONPATH=/home/nathan/workspaces/nth-share/sel/python python3 -m sel
-{"1"=-{"cat"=t"A", "n"=t"1"}, "2"=-{"cat"=t"a", "n"=t"1"}, "3"=-{"cat"=t"A ", "n"=t"1"}}
```

Check PAD SPACE behaviour on MySQL and whether docs mention it

```bash
mysql -h127.0.0.1 -P33069 -uroot -proot sel 2>/dev/null -e "SELECT CAST('A' AS CHAR) COLLATE utf8mb4_bin = CAST('A ' AS CHAR) COLLATE utf8mb4_bin AS pad_eq, 'A' = 'a' AS ci_eq, @@collation_database;" ; cd /home/nathan/workspaces/nth-share/sel && node js/bin/sel.mjs -e '"A" $== "A "'; grep -n "PAD\|pad space\|trailing space" sql/MAP.md docs/SQL-TRANSLATION.md sql/dialects/*.json | head -5
```

```
pad_eq	ci_eq	@@collation_database
1	1	utf8mb4_0900_ai_ci
FALSE
docs/SQL-TRANSLATION.md:438:                'CRC32'  => ['tpl' => 'LPAD(LOWER(HEX(CRC32({0}))), 8, \'0\')',
sql/dialects/postgresql.json:110:    "PADL": {
sql/dialects/postgresql.json:114:    "PADR": {
sql/dialects/ansi.json:238:    "PADL": "not ANSI; set per dialect",
sql/dialects/ansi.json:239:    "PADR": "not ANSI; set per dialect",
```

Check the same statements on PostgreSQL 17, then tear down all containers

```bash
docker images | grep -E "^postgres " | head -3; docker run -d --rm --name sel-verify-L-pg -e POSTGRES_PASSWORD=pg -p 127.0.0.1:54329:5432 --tmpfs /var/lib/postgresql/data postgres:17 >/dev/null 2>&1 && for i in $(seq 1 30); do PGPASSWORD=pg psql -h127.0.0.1 -p54329 -Upostgres -c 'select 1' >/dev/null 2>&1 && break; sleep 2; done; PGPASSWORD=pg psql -h127.0.0.1 -p54329 -Upostgres -q <<'EOF'
CREATE TABLE items (cat VARCHAR(16), v INT);
INSERT INTO items VALUES ('A', 1), ('a', 2), ('A ', 3);
SELECT "cat" AS "cat", COUNT(*) AS "n" FROM "items" GROUP BY "cat";
SELECT "_sub1"."cat" FROM (SELECT * FROM "items" ORDER BY "cat" ASC) "_sub1";
SHOW lc_collate;
EOF
docker rm -f sel-verify-L-pg sel-verify-L sel-verify-L-mysql >/dev/null 2>&1; echo cleaned
```

```
cat | n 
-----+---
 A   | 1
 a   | 1
 A   | 1
(3 rows)

 cat 
-----
 a
 A
 A 
(3 rows)

ERROR:  unrecognized configuration parameter "lc_collate"
cleaned
```

