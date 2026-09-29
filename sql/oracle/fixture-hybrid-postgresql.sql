-- The hybrid-parity fixture (PostgreSQL): eight orders and five customers, chosen so the
-- programs in hybrid.json can tell apart the things a hybrid plan can lose --
-- row keys (ids run 1..8, so a key that survives a FILTER equals the id), a
-- row order (names sort differently by bytes than by id), groups (customer ids
-- 7 7 9 9 11 11 12 12) and ties (amounts and customer ids repeat). No program
-- relies on the database's natural row order: every one that observes an order
-- names a unique ORDER BY key.

DROP TABLE IF EXISTS hy_orders;
DROP TABLE IF EXISTS hy_customers;

CREATE TABLE hy_orders (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL,
  amount INT NOT NULL,
  name VARCHAR(32) NOT NULL
);

CREATE TABLE hy_customers (
  id INT PRIMARY KEY,
  name VARCHAR(32) NOT NULL
);

INSERT INTO hy_orders (id, customer_id, amount, name) VALUES
  (1, 7, 10, 'alpha'),
  (2, 7, 5, 'gamma'),
  (3, 9, 7, 'Alpha'),
  (4, 9, 12, 'ïnü'),
  (5, 11, 3, 'delta'),
  (6, 11, 8, 'beta'),
  (7, 12, 9, 'echo'),
  (8, 12, 4, 'foxtrot');

INSERT INTO hy_customers (id, name) VALUES
  (7, 'ann'),
  (9, 'bob'),
  (11, 'cy'),
  (12, 'dee'),
  (13, 'eve');
