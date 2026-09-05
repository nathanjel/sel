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
  credit_limit  DECIMAL(10,2) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE order_items (
  order_id  INT NOT NULL,
  sku       VARCHAR(32) NOT NULL,
  qty       DECIMAL(10,2) NOT NULL,
  price     DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (order_id, sku),
  CONSTRAINT fk_oi_o FOREIGN KEY (order_id) REFERENCES o(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- The tables are deliberately `utf8mb4_general_ci`, the default a real schema
-- gets by accident. A byte comparison that agrees with SEL here agrees because
-- the map made it agree, not because the column was already case-sensitive.

INSERT INTO o (id, credit_limit) VALUES
  (1, 100.00),   -- ordinary: two small, well-formed lines
  (2,  10.00),   -- one big expensive line, over its limit
  (3, 500.00),   -- no lines at all: every ALL over it is vacuously TRUE
  (4,  50.00),   -- a zero-quantity line
  (5,  50.00);   -- a malformed SKU

INSERT INTO order_items (order_id, sku, qty, price) VALUES
  (1, 'AB-1000', 2.00, 10.00),
  (1, 'CD-2000', 3.00,  5.00),
  (2, 'EF-3000', 5.00, 30.00),
  (4, 'GH-4000', 0.00,  1.00),
  (4, 'IJ-5000', 4.00,  2.00),
  (5, 'bad-sku', 1.00,  3.00),
  (5, 'KL-6000', 6.00,  4.00);
