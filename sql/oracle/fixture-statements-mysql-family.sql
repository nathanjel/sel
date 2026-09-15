-- The statement-parity fixture for MariaDB and MySQL. Five items in four
-- categories that differ only by case -- 'A', 'a', 'B', 'b' -- because the
-- question this fixture exists to ask is whether a grouped or sorted TEXT key
-- is compared by its bytes: the servers' default collations are
-- case-insensitive, and SEL's evaluator is not (review 2026-09-15 finding L).
-- No trailing spaces: utf8mb4_bin is PAD SPACE on both servers, and 'A' vs
-- 'A ' is the text-collation caveat, not this fixture's question.

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
