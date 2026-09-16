-- The statement-parity fixture for MariaDB and MySQL. Five items in four
-- categories that differ only by case -- 'A', 'a', 'B', 'b' -- because the
-- question this fixture exists to ask is whether a grouped or sorted TEXT key
-- is compared by its bytes: the servers' default collations are
-- case-insensitive, and SEL's evaluator is not (review 2026-09-15 finding L).
-- F2 adds empty strings, spaces, trailing spaces and non-ASCII text. These
-- VARCHAR values must remain distinct; a binary PAD SPACE collation is not
-- sufficient. Text identity is not excused by a caveat for these bindings.

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
  (5, 'A', 3),
  (6, '', 6),
  (7, ' ', 7),
  (8, '  ', 8),
  (9, 'a ', 9),
  (10, 'a  ', 10),
  (11, 'ą', 11),
  (12, 'ą ', 12),
  (13, 'A ', 13);
