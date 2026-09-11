#!/usr/bin/env python3
"""
tools/scale-test/generate_data.py
Multi-scale relational dataset generator for SEL stress testing and parity benchmarks.

Supports:
  --scale 1   : ~13.7k rows
  --scale 10  : ~137k rows
  --scale 100 : ~1.37M rows

Tables:
  - categories
  - products
  - customers (with latitude, longitude, and Postgres point / spatial coordinates)
  - orders
  - order_items

Outputs:
  - PostgreSQL schema and data (loaded directly or exported to postgres.sql)
  - MariaDB schema and data (loaded directly or exported to mariadb.sql)
  - JSON dump (dataset-{scale}x.json) for in-memory SEL
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time

SEED = 42

CAT_TEMPLATES = [
    ("ELEC", "Electronics", "0.23"),
    ("BOOK", "Books", "0.08"),
    ("CLOTH", "Clothing", "0.23"),
    ("HOME", "Home & Kitchen", "0.23"),
    ("FOOD", "Groceries", "0.05"),
    ("AUTO", "Automotive", "0.23"),
    ("BEAU", "Beauty", "0.23"),
    ("SPRT", "Sports", "0.23"),
    ("TOYS", "Toys", "0.23"),
    ("GARD", "Garden", "0.08"),
    ("OFFC", "Office Supplies", "0.23"),
    ("PETS", "Pet Supplies", "0.23"),
    ("JEWL", "Jewelry", "0.23"),
    ("TOOL", "Tools & Hardware", "0.23"),
    ("MUSC", "Musical Instruments", "0.23"),
    ("HEAL", "Health & Wellness", "0.08"),
    ("BABY", "Baby Products", "0.08"),
    ("OUTD", "Outdoor Recreation", "0.23"),
    ("ARTS", "Arts & Crafts", "0.23"),
    ("INDL", "Industrial & Scientific", "0.23"),
]

TIERS = ["BRONZE", "SILVER", "GOLD", "PLATINUM"]
TIER_WEIGHTS = [0.45, 0.30, 0.15, 0.10]
COUNTRIES = ["US", "DE", "FR", "PL", "GB", "JP"]
COUNTRY_WEIGHTS = [0.35, 0.20, 0.15, 0.15, 0.10, 0.05]

COUNTRY_GEO = {
    "US": (37.0902, -95.7129),
    "DE": (51.1657, 10.4515),
    "FR": (46.2276, 2.2137),
    "PL": (51.9194, 19.1451),
    "GB": (55.3781, -3.4360),
    "JP": (36.2048, 138.2529),
}

FIRST_NAMES = ["Alice", "Bob", "Charlie", "David", "Emma", "Fiona", "George", "Hannah",
               "Ivan", "Julia", "Klaus", "Laura", "Marcin", "Nina", "Oliver", "Paula",
               "Quinn", "Rachel", "Stefan", "Tina", "Ulrich", "Valerie", "Will", "Xenia"]
LAST_NAMES = ["Smith", "Müller", "Dupont", "Kowalski", "Brown", "Tanaka", "Wagner", "Nowak",
              "Schmidt", "Lefebvre", "Wiśniewski", "Taylor", "Sato", "Fischer", "Kaminski"]

STATUSES = ["COMPLETED", "PENDING", "CANCELLED", "REFUNDED"]
STATUS_WEIGHTS = [0.70, 0.15, 0.10, 0.05]
DISCOUNTS = ["0.00", "5.00", "10.00", "20.00", "50.00"]


def generate_dataset(scale=1):
    random.seed(SEED)
    
    num_cats = min(200, 10 * scale) if scale > 1 else 10
    num_prods = 200 * scale
    num_custs = 1000 * scale
    num_orders = 3500 * scale
    num_items = 9000 * scale

    print(f"Generating scale={scale}x:")
    print(f"  categories:  {num_cats:,}")
    print(f"  products:    {num_prods:,}")
    print(f"  customers:   {num_custs:,}")
    print(f"  orders:      {num_orders:,}")
    print(f"  order_items: {num_items:,}")
    total_rows = num_cats + num_prods + num_custs + num_orders + num_items
    print(f"  TOTAL:       {total_rows:,} rows")

    # 1. Categories
    categories = []
    for i in range(1, num_cats + 1):
        tmpl = CAT_TEMPLATES[(i - 1) % len(CAT_TEMPLATES)]
        code = f"{tmpl[0]}_{i}" if i > len(CAT_TEMPLATES) else tmpl[0]
        name = f"{tmpl[1]} {i}" if i > len(CAT_TEMPLATES) else tmpl[1]
        vat = tmpl[2]
        categories.append({"id": i, "code": code, "name": name, "vat_rate": vat})

    # 2. Products
    products = []
    for i in range(1, num_prods + 1):
        cat = categories[(i - 1) % num_cats]
        cat_id = cat["id"]
        cat_code = cat["code"]
        sku = f"SKU-{cat_code}-{i:05d}"
        price_val = round(random.uniform(5.0, 500.0), 2)
        price = f"{price_val:.2f}"
        is_active = 1 if random.random() < 0.90 else 0
        name = f"{cat_code} Item {i}"
        products.append({
            "id": i,
            "sku": sku,
            "name": name,
            "category_id": cat_id,
            "price": price,
            "is_active": is_active,
        })

    # 3. Customers (with spatial latitude and longitude)
    customers = []
    for i in range(1, num_custs + 1):
        tier = random.choices(TIERS, weights=TIER_WEIGHTS)[0]
        country = random.choices(COUNTRIES, weights=COUNTRY_WEIGHTS)[0]
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        name = f"{fn} {ln} {i}"
        year = random.randint(2021, 2026)
        
        # Spatial coords jittered around country center
        center_lat, center_lon = COUNTRY_GEO[country]
        lat = round(center_lat + random.uniform(-3.5, 3.5), 6)
        lon = round(center_lon + random.uniform(-4.5, 4.5), 6)
        
        customers.append({
            "id": i,
            "name": name,
            "tier": tier,
            "country": country,
            "created_year": year,
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
        })

    # 4. Orders
    active_cust_limit = int(num_custs * 0.95)
    orders = []
    for i in range(1, num_orders + 1):
        cust_id = random.randint(1, active_cust_limit)
        status = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
        disc = random.choice(DISCOUNTS)
        year = random.randint(2024, 2026)
        orders.append({
            "id": i,
            "customer_id": cust_id,
            "status": status,
            "discount": disc,
            "order_year": year,
        })

    # 5. Order Items
    prod_map = {p["id"]: p["price"] for p in products}
    active_order_limit = int(num_orders * 0.98)
    active_prod_limit = int(num_prods * 0.95)
    order_items = []
    for i in range(1, num_items + 1):
        order_id = random.randint(1, active_order_limit)
        prod_id = random.randint(1, active_prod_limit)
        qty = random.randint(1, 8)
        unit_price = prod_map[prod_id]
        order_items.append({
            "id": i,
            "order_id": order_id,
            "product_id": prod_id,
            "quantity": qty,
            "unit_price": unit_price,
        })

    dataset = {
        "categories": categories,
        "products": products,
        "customers": customers,
        "orders": orders,
        "order_items": order_items,
    }
    return dataset


def write_sql_script(dataset, dialect, filepath):
    lines = []
    batch_size = 1000

    def insert_chunks(tbl, cols, rows):
        col_str = ", ".join(cols)
        for b in range(0, len(rows), batch_size):
            chunk = rows[b:b+batch_size]
            val_strs = []
            for r in chunk:
                fmtd = []
                for c in cols:
                    v = r[c]
                    if isinstance(v, str):
                        escaped = v.replace("'", "''")
                        fmtd.append(f"'{escaped}'")
                    else:
                        fmtd.append(str(v))
                val_strs.append(f"({', '.join(fmtd)})")
            lines.append(f"INSERT INTO {tbl} ({col_str}) VALUES\n" + ",\n".join(val_strs) + ";")

    if dialect == "postgres":
        lines.append("BEGIN;")
        lines.append("DROP TABLE IF EXISTS order_items CASCADE;")
        lines.append("DROP TABLE IF EXISTS orders CASCADE;")
        lines.append("DROP TABLE IF EXISTS products CASCADE;")
        lines.append("DROP TABLE IF EXISTS customers CASCADE;")
        lines.append("DROP TABLE IF EXISTS categories CASCADE;")

        lines.append("""
CREATE TABLE categories (
    id INT PRIMARY KEY,
    code VARCHAR(16) NOT NULL,
    name VARCHAR(64) NOT NULL,
    vat_rate NUMERIC(4,2) NOT NULL
);
CREATE TABLE products (
    id INT PRIMARY KEY,
    sku VARCHAR(32) NOT NULL,
    name VARCHAR(128) NOT NULL,
    category_id INT NOT NULL REFERENCES categories(id),
    price NUMERIC(10,2) NOT NULL,
    is_active INT NOT NULL
);
CREATE INDEX idx_products_cat ON products(category_id);

CREATE TABLE customers (
    id INT PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    tier VARCHAR(16) NOT NULL,
    country VARCHAR(8) NOT NULL,
    created_year INT NOT NULL,
    latitude NUMERIC(9,6) NOT NULL,
    longitude NUMERIC(9,6) NOT NULL,
    location POINT GENERATED ALWAYS AS (point(longitude, latitude)) STORED
);
CREATE INDEX idx_customers_tier ON customers(tier);
CREATE INDEX idx_customers_country ON customers(country);
CREATE INDEX idx_customers_loc ON customers USING gist (location);

CREATE TABLE orders (
    id INT PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(id),
    status VARCHAR(16) NOT NULL,
    discount NUMERIC(10,2) NOT NULL,
    order_year INT NOT NULL
);
CREATE INDEX idx_orders_cust ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_year ON orders(order_year);

CREATE TABLE order_items (
    id INT PRIMARY KEY,
    order_id INT NOT NULL REFERENCES orders(id),
    product_id INT NOT NULL REFERENCES products(id),
    quantity INT NOT NULL,
    unit_price NUMERIC(10,2) NOT NULL
);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_prod ON order_items(product_id);
""")
        insert_chunks("categories", ["id", "code", "name", "vat_rate"], dataset["categories"])
        insert_chunks("products", ["id", "sku", "name", "category_id", "price", "is_active"], dataset["products"])
        insert_chunks("customers", ["id", "name", "tier", "country", "created_year", "latitude", "longitude"], dataset["customers"])
        insert_chunks("orders", ["id", "customer_id", "status", "discount", "order_year"], dataset["orders"])
        insert_chunks("order_items", ["id", "order_id", "product_id", "quantity", "unit_price"], dataset["order_items"])
        lines.append("COMMIT;")

    else:  # mariadb
        lines.append("SET FOREIGN_KEY_CHECKS=0;")
        lines.append("DROP TABLE IF EXISTS order_items;")
        lines.append("DROP TABLE IF EXISTS orders;")
        lines.append("DROP TABLE IF EXISTS products;")
        lines.append("DROP TABLE IF EXISTS customers;")
        lines.append("DROP TABLE IF EXISTS categories;")
        lines.append("SET FOREIGN_KEY_CHECKS=1;")

        lines.append("""
CREATE TABLE categories (
    id INT PRIMARY KEY,
    code VARCHAR(16) NOT NULL,
    name VARCHAR(64) NOT NULL,
    vat_rate DECIMAL(4,2) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE products (
    id INT PRIMARY KEY,
    sku VARCHAR(32) NOT NULL,
    name VARCHAR(128) NOT NULL,
    category_id INT NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    is_active INT NOT NULL,
    INDEX (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customers (
    id INT PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    tier VARCHAR(16) NOT NULL,
    country VARCHAR(8) NOT NULL,
    created_year INT NOT NULL,
    latitude DECIMAL(9,6) NOT NULL,
    longitude DECIMAL(9,6) NOT NULL,
    INDEX (country),
    INDEX (tier),
    INDEX (latitude, longitude)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
    id INT PRIMARY KEY,
    customer_id INT NOT NULL,
    status VARCHAR(16) NOT NULL,
    discount DECIMAL(10,2) NOT NULL,
    order_year INT NOT NULL,
    INDEX (customer_id),
    INDEX (status),
    INDEX (order_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_items (
    id INT PRIMARY KEY,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    INDEX (order_id),
    INDEX (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""")
        lines.append("START TRANSACTION;")
        insert_chunks("categories", ["id", "code", "name", "vat_rate"], dataset["categories"])
        insert_chunks("products", ["id", "sku", "name", "category_id", "price", "is_active"], dataset["products"])
        insert_chunks("customers", ["id", "name", "tier", "country", "created_year", "latitude", "longitude"], dataset["customers"])
        insert_chunks("orders", ["id", "customer_id", "status", "discount", "order_year"], dataset["orders"])
        insert_chunks("order_items", ["id", "order_id", "product_id", "quantity", "unit_price"], dataset["order_items"])
        lines.append("COMMIT;")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {filepath} ({len(lines)} lines, {os.path.getsize(filepath)/1024/1024:.2f} MB)")


def load_postgres(filepath):
    print(f"Loading {filepath} into PostgreSQL (sel-pg)...")
    t0 = time.perf_counter()
    with open(filepath, "r", encoding="utf-8") as f:
        res = subprocess.run(
            ["docker", "exec", "-i", "sel-pg", "psql", "-U", "postgres", "-d", "sel_oracle"],
            stdin=f, capture_output=True, text=True
        )
    if res.returncode != 0:
        raise RuntimeError(f"PostgreSQL load failed: {res.stderr}")
    print(f"PostgreSQL load completed in {time.perf_counter() - t0:.2f}s")


def load_mariadb(filepath):
    print(f"Loading {filepath} into MariaDB...")
    t0 = time.perf_counter()
    with open(filepath, "r", encoding="utf-8") as f:
        res = subprocess.run(
            ["mariadb", "-D", "sel_oracle"],
            stdin=f, capture_output=True, text=True
        )
    if res.returncode != 0:
        raise RuntimeError(f"MariaDB load failed: {res.stderr}")
    print(f"MariaDB load completed in {time.perf_counter() - t0:.2f}s")


def main():
    parser = argparse.ArgumentParser(description="Multi-scale relational dataset generator for SEL.")
    parser.add_argument("--scale", type=int, default=1, choices=[1, 10, 100], help="Dataset scale factor (1, 10, 100)")
    parser.add_argument("--out-dir", type=str, default="tools/scale-test", help="Output directory")
    parser.add_argument("--load-db", action="store_true", help="Automatically load data into Postgres and MariaDB")
    parser.add_argument("--skip-json", action="store_true", help="Skip writing JSON file")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    t_start = time.perf_counter()
    dataset = generate_dataset(args.scale)

    scale_suffix = f"-{args.scale}x" if args.scale > 1 else ""

    # JSON export
    if not args.skip_json:
        json_path = os.path.join(args.out_dir, f"dataset{scale_suffix}.json")
        print(f"Writing {json_path}...")
        t0 = time.perf_counter()
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(dataset, f)
        print(f"Wrote {json_path} ({os.path.getsize(json_path)/1024/1024:.2f} MB) in {time.perf_counter() - t0:.2f}s")

    # SQL exports
    pg_sql = os.path.join(args.out_dir, f"postgres{scale_suffix}.sql")
    ma_sql = os.path.join(args.out_dir, f"mariadb{scale_suffix}.sql")
    
    write_sql_script(dataset, "postgres", pg_sql)
    write_sql_script(dataset, "mariadb", ma_sql)

    if args.load_db:
        load_postgres(pg_sql)
        load_mariadb(ma_sql)

    print(f"All done for scale={args.scale}x in {time.perf_counter() - t_start:.2f}s!")


if __name__ == "__main__":
    main()
