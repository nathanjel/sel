-- The shop of examples/sql-functions, for PostgreSQL: a catalogue, orders, VAT
-- rates, and the database functions the application's own SEL functions are
-- spelled as. Loaded by tools/check-usage.sh.
DROP TABLE IF EXISTS order_lines, orders, products, vat_rates;
DROP FUNCTION IF EXISTS slug(text), margin_pct(numeric, numeric), vat_rate(text, text), shipping_cost(numeric, text);

CREATE TABLE products (product_id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL, cost NUMERIC(10,2) NOT NULL, weight_kg NUMERIC(6,2) NOT NULL,
  tag1 TEXT NOT NULL, tag2 TEXT NOT NULL, tag3 TEXT NOT NULL);
CREATE TABLE orders (order_id INTEGER PRIMARY KEY, country CHAR(2) NOT NULL);
CREATE TABLE order_lines (order_id INTEGER NOT NULL REFERENCES orders, line_no INTEGER NOT NULL,
  product_id INTEGER NOT NULL REFERENCES products, qty INTEGER NOT NULL, PRIMARY KEY (order_id, line_no));
CREATE TABLE vat_rates (country CHAR(2) NOT NULL, category TEXT NOT NULL, rate NUMERIC(5,4) NOT NULL,
  PRIMARY KEY (country, category));

-- SLUG: a plain value mapping, as an inline SQL function.
CREATE FUNCTION slug(t text) RETURNS text IMMUTABLE LANGUAGE sql AS $$
  SELECT trim(both '-' from regexp_replace(lower(t), '[^a-z0-9]+', '-', 'g'))
$$;

-- MARGIN_PCT: two numbers in, one out, rounded half away from zero as SEL's ROUND does
-- (PostgreSQL's round() on numeric does the same).
CREATE FUNCTION margin_pct(price numeric, cost numeric) RETURNS numeric IMMUTABLE LANGUAGE sql AS $$
  SELECT round((price - cost) * 100 / price, 1)
$$;

-- VAT_RATE: a lookup in a table -- the rate for the category, else the country's
-- default ('*'), else zero. STABLE, because it reads data.
CREATE FUNCTION vat_rate(c text, k text) RETURNS numeric STABLE LANGUAGE sql AS $$
  SELECT coalesce(
    (SELECT rate FROM vat_rates WHERE country = c AND category = k),
    (SELECT rate FROM vat_rates WHERE country = c AND category = '*'),
    0)
$$;

-- SHIPPING_COST: a stored function with logic -- weight tiers, doubled abroad.
CREATE FUNCTION shipping_cost(weight numeric, c text) RETURNS numeric IMMUTABLE LANGUAGE plpgsql AS $$
DECLARE
  base numeric;
BEGIN
  IF weight <= 1 THEN base := 4.90;
  ELSIF weight <= 5 THEN base := 9.90;
  ELSIF weight <= 20 THEN base := 19.90;
  ELSE base := 49.00;
  END IF;
  IF c <> 'PL' THEN
    RETURN base * 2;
  END IF;
  RETURN base;
END
$$;

INSERT INTO products VALUES
  (1, 'Cast Iron Pan', 'kitchen', 149.90, 81.00, 2.80, 'gift', 'bestseller', ''),
  (2, 'Chef''s Knife (8")', 'kitchen', 219.00, 140.00, 0.40, 'gift', '', ''),
  (3, 'The Pragmatic Garden', 'books', 24.90, 18.00, 0.60, '', '', 'clearance'),
  (4, 'Decimal Tales -- 2nd edition', 'books', 39.00, 21.50, 0.70, 'gift', 'new', ''),
  (5, 'Oak Planter, large', 'garden', 74.00, 52.00, 12.00, '', 'bestseller', ''),
  (6, 'Rain Barrel 200L', 'garden', 189.00, 120.00, 24.50, 'clearance', '', ''),
  (7, 'Harbour Lights', 'games', 129.99, 70.00, 1.50, 'gift', 'new', 'bestseller'),
  (8, 'Cardinal Rules', 'games', 59.50, 41.00, 0.90, '', '', '');
INSERT INTO orders VALUES (1, 'PL'), (2, 'DE'), (3, 'PL'), (4, 'FR'), (5, 'DE'), (6, 'PL');
INSERT INTO order_lines VALUES
  (1, 1, 1, 1), (1, 2, 3, 2), (2, 1, 7, 1), (2, 2, 4, 3), (3, 1, 5, 2), (3, 2, 2, 1),
  (4, 1, 6, 1), (4, 2, 8, 2), (5, 1, 1, 2), (6, 1, 3, 1), (6, 2, 7, 1), (6, 3, 4, 1);
INSERT INTO vat_rates VALUES
  ('PL', '*', 0.2300), ('PL', 'books', 0.0500),
  ('DE', '*', 0.1900), ('DE', 'books', 0.0700),
  ('FR', '*', 0.2000), ('FR', 'books', 0.0550);
