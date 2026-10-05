-- The cross-host statement fixture (php/bin/sqlo `cross`): the two relations
-- every host's sqlfuzz binds, ORDERS and CUSTOMERS. They share the field names `id` and `name` with different
-- values, so a read that resolves to the wrong relation answers different rows;
-- order 4 has no customer, and customer 40 no order, for LINK_LEFT.

DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;

CREATE TABLE orders (
  id          INT PRIMARY KEY,
  customer_id INT NOT NULL,
  amount      INT NOT NULL,
  name        VARCHAR(20) COLLATE utf8mb4_bin NOT NULL
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE customers (
  id   INT PRIMARY KEY,
  name VARCHAR(20) COLLATE utf8mb4_bin NOT NULL
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

INSERT INTO orders (id, customer_id, amount, name) VALUES
  (1, 10, 5, 'pen'),
  (2, 10, 7, 'ink'),
  (3, 20, 2, 'cap'),
  (4, 30, 9, 'box');

INSERT INTO customers (id, name) VALUES
  (10, 'Ann'),
  (20, 'Bob'),
  (40, 'Cid');
