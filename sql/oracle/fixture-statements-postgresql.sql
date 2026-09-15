-- The statement-parity fixture for PostgreSQL: the same five items as
-- fixture-statements-mysql-family.sql. PostgreSQL's default collation is
-- case-sensitive but not byte-ordered (en_US puts 'a' before 'B' before 'b'),
-- so the sort statements are what it answers for.

DROP TABLE IF EXISTS grp_items;

CREATE TABLE grp_items (
  id  INT PRIMARY KEY,
  cat VARCHAR(16) NOT NULL,
  v   INT NOT NULL
);

INSERT INTO grp_items (id, cat, v) VALUES
  (1, 'A', 1),
  (2, 'a', 2),
  (3, 'B', 4),
  (4, 'b', 5),
  (5, 'A', 3);
