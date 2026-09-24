-- The customers of examples/sql-conditions (naive bindings), for SQLite. Loaded by tools/check-usage.sh.
DROP TABLE IF EXISTS customers;
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL, country CHAR(2) NOT NULL, postcode TEXT NOT NULL, credit_limit TEXT NOT NULL, tier TEXT NOT NULL);
INSERT INTO customers VALUES
  (1, 'Anna Nowak', 'anna@example.pl', 'PL', '31-874', '2500.00', 'gold'),
  (2, 'Bruno Keller', 'bruno@example.de', 'DE', '10115', '800.00', 'standard'),
  (3, 'Chloé Martin', '', 'FR', '69002', '1200.00', 'silver'),
  (4, 'Dawid Wiśniewski', 'dawid@example.pl', 'PL', '00-950', '950.00', 'standard'),
  (5, 'Eva Lindqvist', 'eva.lindqvist@example.se', 'SE', '211 45', '5000.00', 'platinum'),
  (6, 'Filip Kowalczyk', 'filip(at)example.pl', 'PL', '30-0012', '1000.00', 'silver'),
  (7, 'Greta Hansen', 'greta@example.dk', 'DK', '2100', '300.00', 'standard'),
  (8, 'Hugo Moreau', 'hugo@example.fr', 'FR', '75001', '1500.00', 'gold'),
  (9, 'Irena Zając-Kowalewska', 'irena@example.pl', 'PL', '02-797', '1800.00', 'gold'),
  (10, 'Jan Nowak', '  ', 'PL', '80-001', '400.00', 'standard');
