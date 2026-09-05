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
  credit_limit  NUMERIC NOT NULL
);

CREATE TABLE order_items (
  order_id  INTEGER NOT NULL REFERENCES o(id),
  sku       TEXT NOT NULL,
  qty       NUMERIC NOT NULL,
  price     NUMERIC NOT NULL,
  PRIMARY KEY (order_id, sku)
);

INSERT INTO o (id, credit_limit) VALUES
  (1, 100.00),
  (2,  10.00),
  (3, 500.00),
  (4,  50.00),
  (5,  50.00);

INSERT INTO order_items (order_id, sku, qty, price) VALUES
  (1, 'AB-1000', 2.00, 10.00),
  (1, 'CD-2000', 3.00,  5.00),
  (2, 'EF-3000', 5.00, 30.00),
  (4, 'GH-4000', 0.00,  1.00),
  (4, 'IJ-5000', 4.00,  2.00),
  (5, 'bad-sku', 1.00,  3.00),
  (5, 'KL-6000', 6.00,  4.00);
