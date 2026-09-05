-- The row-parity fixture for PostgreSQL: the same five orders and seven lines as
-- the other two, in PostgreSQL's spelling.
--
-- NUMERIC(10,2) is a real exact decimal with a real scale, so the values SEL is
-- handed here are the ones it would be handed by MariaDB and not the ones SQLite
-- rounds off. The identifiers are double-quoted rather than backticked, which is
-- also why rows.json carries the correlation clause per dialect: `correlate` is
-- raw SQL by design -- it is the one place a host says something the map cannot
-- know -- and raw SQL does not travel.

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS o;

CREATE TABLE o (
  id            INTEGER PRIMARY KEY,
  credit_limit  NUMERIC(10,2) NOT NULL
);

CREATE TABLE order_items (
  order_id  INTEGER NOT NULL REFERENCES o(id),
  sku       TEXT NOT NULL,
  qty       NUMERIC(10,2) NOT NULL,
  price     NUMERIC(10,2) NOT NULL,
  PRIMARY KEY (order_id, sku)
);

INSERT INTO o (id, credit_limit) VALUES
  (1, 100.00), (2, 10.00), (3, 500.00), (4, 50.00), (5, 50.00);

INSERT INTO order_items (order_id, sku, qty, price) VALUES
  (1, 'AB-1000', 2.00, 10.00),
  (1, 'CD-2000', 3.00,  5.00),
  (2, 'EF-3000', 5.00, 30.00),
  (4, 'GH-4000', 0.00,  1.00),
  (4, 'IJ-5000', 4.00,  2.00),
  (5, 'bad-sku', 1.00,  3.00),
  (5, 'KL-6000', 6.00,  4.00);
