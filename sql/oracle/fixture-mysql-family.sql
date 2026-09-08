-- The row-parity fixture for the MySQL family -- MariaDB and MySQL both, whose
-- DDL and type systems agree here. The SQLite spelling is beside it.
--
-- The row-parity fixture: a table the translated WHERE clause selects from, and
-- the same data the evaluator is handed as a SEL context.
--
-- This file exists because the original one did not. The M3 row-parity check ran
-- against a database called `sel_m3` created by hand at a shell, and three commit
-- messages cite its result. The database is gone and the numbers cannot be
-- reproduced. See docs/SQL-TESTING.md §9.
--
-- The seed is chosen so that every rule in rows.json selects SOME BUT NOT ALL
-- orders. A rule both sides answer `[]` to agrees about nothing, and the driver
-- fails a run in which any rule is degenerate — the check has to be able to fail
-- before its passing means anything.

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS o;

CREATE TABLE o (
  id            INT PRIMARY KEY,
  credit_limit  DECIMAL(10,2) NOT NULL,
  code          VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE order_items (
  order_id  INT NOT NULL,
  sku       VARCHAR(32) NOT NULL,
  qty       DECIMAL(10,2) NOT NULL,
  price     DECIMAL(10,2) NOT NULL,
  note      VARCHAR(32) NULL,
  value     VARCHAR(32) NOT NULL,
  PRIMARY KEY (order_id, sku),
  CONSTRAINT fk_oi_o FOREIGN KEY (order_id) REFERENCES o(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- The tables are deliberately `utf8mb4_general_ci`, the default a real schema
-- gets by accident. A byte comparison that agrees with SEL here agrees because
-- the map made it agree, not because the column was already case-sensitive.

INSERT INTO o (id, credit_limit, code) VALUES
  (1, 100.00, '25'),      -- ordinary: two small, well-formed lines
  (2,  10.00, '25/298'),  -- one big expensive line, over its limit
  (3, 500.00, 'x'),       -- no lines at all: every ALL over it is vacuously TRUE
  (4,  50.00, ''),        -- a zero-quantity line
  (5,  50.00, '30');      -- a malformed SKU

-- `code` and `value` hold text that is not a number, which is the shape the
-- first external user of this layer ran into: an EAV table whose values are all
-- VARCHAR, compared as numbers by a rule. Until they existed every column in
-- this fixture was declared NUM or TEXT and every numeric comparison was over a
-- NUM one, so the question docs/SQL-KINDS.md exists to answer -- does SQL report
-- a match for a row SEL refuses? -- could not be asked here at all.
--
-- '25/298' is theirs, near enough: MariaDB reads the numeric prefix and answers
-- 25, so `CODE == 25` matched it before the guard. 'x' and '' both cast to 0,
-- which is how `T == 0` matched every row.


-- `note` is NULLABLE, and it is the only NULL anywhere in this fixture or in
-- expressions.selo. That absence was itself a finding: SEL has no null, so
-- every divergence SQL's three-valued logic can cause was unmeasured for five
-- milestones. The `inRelation` skeleton was missing the IS TRUE fold that
-- `all` and `any` carry, and nothing could show it.

-- The non-numeric `value` sits on AB-1000, which sorts FIRST under every
-- collation in this directory -- utf8mb4_general_ci here, BINARY in SQLite, the
-- container's locale in PostgreSQL. That is deliberate. ALL and ANY
-- short-circuit, so which element SEL reaches first decides whether it answers
-- or refuses, and 'bad-sku' vs 'KL-6000' sorts one way case-insensitively and
-- the other way by bytes. A rule whose SEL answer depends on the server's
-- collation is a rule that measures the collation.
INSERT INTO order_items (order_id, sku, qty, price, note, value) VALUES
  (1, 'AB-1000', 2.00, 10.00, 'flag',    'x'),
  (1, 'CD-2000', 3.00,  5.00, NULL,      '9'),
  (2, 'EF-3000', 5.00, 30.00, NULL,      'n/a'),
  (4, 'GH-4000', 0.00,  1.00, 'GH-4000', '0'),
  (4, 'IJ-5000', 4.00,  2.00, NULL,      '4'),
  (5, 'bad-sku', 1.00,  3.00, 'flag',    '1'),
  (5, 'KL-6000', 6.00,  4.00, NULL,      '2');
