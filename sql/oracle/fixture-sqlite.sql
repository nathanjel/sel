-- The row-parity fixture for SQLite: the same five orders and seven lines as
-- fixture-mariadb.sql, in SQLite's spelling.
--
-- Two differences worth knowing about, both of which make this the harder side
-- of the comparison rather than the easier one:
--
--   * There is no DECIMAL. `qty` and `price` are declared NUMERIC, which SQLite
--     treats as a type AFFINITY, not a type: '2.00' stored into it becomes the
--     integer 2, and 10.00 becomes 10. So the values SEL is handed here differ
--     in scale from the ones MariaDB hands it, and the rules still have to
--     select the same orders. They do, because every rule compares numerically.
--
--   * There is no collation to get wrong. SQLite's default is BINARY, so the
--     "sku matches, wrong case" rule answers [] on both sides without any help
--     from the map -- where MariaDB needed COLLATE to be talked into it.

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS o;

CREATE TABLE o (
  id            INTEGER PRIMARY KEY,
  credit_limit  NUMERIC NOT NULL,
  code          TEXT NOT NULL
);

CREATE TABLE order_items (
  order_id  INTEGER NOT NULL REFERENCES o(id),
  sku       TEXT NOT NULL,
  qty       NUMERIC NOT NULL,
  price     NUMERIC NOT NULL,
  note      TEXT NULL,
  value     TEXT NOT NULL,
  PRIMARY KEY (order_id, sku)
);

INSERT INTO o (id, credit_limit, code) VALUES
  (1, 100.00, '25'),
  (2,  10.00, '25/298'),
  (3, 500.00, 'x'),
  (4,  50.00, ''),
  (5,  50.00, '30');

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

-- The non-numeric `value` is on AB-1000 because it sorts first under every
-- collation here; ALL and ANY short-circuit, so element order decides whether
-- SEL answers or refuses. See the MySQL-family fixture for the long version.
INSERT INTO order_items (order_id, sku, qty, price, note, value) VALUES
  (1, 'AB-1000', 2.00, 10.00, 'flag',    'x'),
  (1, 'CD-2000', 3.00,  5.00, NULL,      '9'),
  (2, 'EF-3000', 5.00, 30.00, NULL,      'n/a'),
  (4, 'GH-4000', 0.00,  1.00, 'GH-4000', '0'),
  (4, 'IJ-5000', 4.00,  2.00, NULL,      '4'),
  (5, 'bad-sku', 1.00,  3.00, 'flag',    '1'),
  (5, 'KL-6000', 6.00,  4.00, NULL,      '2');
