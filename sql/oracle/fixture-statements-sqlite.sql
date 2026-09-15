-- The statement-parity fixture for SQLite: the same five items. SQLite's
-- default collation is BINARY, so it agrees with SEL without help from the
-- map -- which makes it the control, not the test.

DROP TABLE IF EXISTS grp_items;

CREATE TABLE grp_items (
  id  INTEGER PRIMARY KEY,
  cat TEXT NOT NULL,
  v   INTEGER NOT NULL
);

INSERT INTO grp_items (id, cat, v) VALUES
  (1, 'A', 1),
  (2, 'a', 2),
  (3, 'B', 4),
  (4, 'b', 5),
  (5, 'A', 3);
