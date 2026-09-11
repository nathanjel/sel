;;;; scale-test/sel_benchmarks.lisp
;;;; Executes the 6 SEL scale & parity scenarios:
;;;; Translates queries to SQL (PostgreSQL & MariaDB), runs in-memory SEL,
;;;; and outputs JSON with SQL, in-memory results, and timing.

(in-package #:cl-user)

(require :asdf)
(let ((setup (merge-pathnames "quicklisp/setup.lisp" (user-homedir-pathname))))
  (when (probe-file setup)
    (load setup)))
(push (truename "lisp/") asdf:*central-registry*)

(eval-when (:compile-toplevel :load-toplevel :execute)
  (ql:quickload :sel-lang/sql :silent t)
  (ql:quickload :yason :silent t))

;; Register custom SEL functions
(sel:register-builtin "CUSTOM_VIP_SCORE" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((tier (sel:as-text (sel::args-val a 0)))
           (year-str (sel:as-text (sel::args-val a 1)))
           (year (or (ignore-errors (parse-integer (first (uiop:split-string year-str :separator ".")))) 2024))
           (base (cond ((string= tier "PLATINUM") 100)
                       ((string= tier "GOLD") 50)
                       ((string= tier "SILVER") 25)
                       (t 10))))
      (sel:make-num (format nil "~D" (+ base (* (- 2026 year) 5)))))))

(sel:register-builtin "HOST_RISK_SCORE" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((country (sel:as-text (sel::args-val a 0)))
           (disc-str (sel:as-text (sel::args-val a 1)))
           (disc (or (ignore-errors (parse-integer (first (uiop:split-string disc-str :separator ".")))) 0))
           (base (if (string= country "US") 30 10)))
      (sel:make-num (format nil "~D" (+ base (* disc 2)))))))

(defparameter *dataset-file* (or (uiop:getenv "SEL_DATASET_FILE") "tools/scale-test/dataset-10x.json"))

(defun build-schema-bindings (dialect)
  (let* ((categories (sel.sql:binding-relation "categories" "categories"
                       (list (cons "ID" (sel.sql:binding-column "id" "categories" :num))
                             (cons "CODE" (sel.sql:binding-column "code" "categories" :text))
                             (cons "NAME" (sel.sql:binding-column "name" "categories" :text))
                             (cons "VAT_RATE" (sel.sql:binding-column "vat_rate" "categories" :num)))))
         (products (sel.sql:binding-relation "products" "products"
                     (list (cons "ID" (sel.sql:binding-column "id" "products" :num))
                           (cons "SKU" (sel.sql:binding-column "sku" "products" :text))
                           (cons "NAME" (sel.sql:binding-column "name" "products" :text))
                           (cons "CATEGORY_ID" (sel.sql:binding-column "category_id" "products" :num))
                           (cons "PRICE" (sel.sql:binding-column "price" "products" :num))
                           (cons "IS_ACTIVE" (sel.sql:binding-column "is_active" "products" :num)))))
         (spatial-field (if (string-equal dialect "postgresql")
                            (cons "DIST_BERLIN" (sel.sql:binding-raw "ROUND((customers.location <-> point(13.404954, 52.520008))::numeric, 6)" :num))
                            (cons "DIST_BERLIN" (sel.sql:binding-raw "ROUND(ST_Distance(POINT(customers.longitude, customers.latitude), POINT(13.404954, 52.520008)), 6)" :num))))
         (customers (sel.sql:binding-relation "customers" "customers"
                      (list (cons "ID" (sel.sql:binding-column "id" "customers" :num))
                            (cons "NAME" (sel.sql:binding-column "name" "customers" :text))
                            (cons "TIER" (sel.sql:binding-column "tier" "customers" :text))
                            (cons "COUNTRY" (sel.sql:binding-column "country" "customers" :text))
                            (cons "CREATED_YEAR" (sel.sql:binding-column "created_year" "customers" :num))
                            (cons "LATITUDE" (sel.sql:binding-column "latitude" "customers" :num))
                            (cons "LONGITUDE" (sel.sql:binding-column "longitude" "customers" :num))
                            spatial-field)))
         (orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders" :num))
                         (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "orders" :num))
                         (cons "STATUS" (sel.sql:binding-column "status" "orders" :text))
                         (cons "DISCOUNT" (sel.sql:binding-column "discount" "orders" :num))
                         (cons "ORDER_YEAR" (sel.sql:binding-column "order_year" "orders" :num)))))
         (order-items (sel.sql:binding-relation "order_items" "order_items"
                        (list (cons "ID" (sel.sql:binding-column "id" "order_items" :num))
                              (cons "ORDER_ID" (sel.sql:binding-column "order_id" "order_items" :num))
                              (cons "PRODUCT_ID" (sel.sql:binding-column "product_id" "order_items" :num))
                              (cons "QUANTITY" (sel.sql:binding-column "quantity" "order_items" :num))
                              (cons "UNIT_PRICE" (sel.sql:binding-column "unit_price" "order_items" :num))))))
    (list (cons "CATEGORIES" categories)
          (cons "PRODUCTS" products)
          (cons "CUSTOMERS" customers)
          (cons "ORDERS" orders)
          (cons "ORDER_ITEMS" order-items))))

(defparameter *scenarios*
  (list
   (list :id "scenario1"
         :name "Scenario 1: Deep 14-Stage Chained Pipeline"
         :desc "Chains 14 operations across 4 tables: 2 subquery wraps, 3 joins, 4 filters, 2 maps, aggregation, having, sort, take."
         :query "ORDERS .> FILTER(_['status'] $== 'COMPLETED')
                        .> FILTER(_['order_year'] >= 2025)
                        .> MAP(RECORD('order_id', _['id'], 'discount', _['discount']))
                        .> LINK(ORDER_ITEMS, _1['order_id'] == _2['order_id'])
                        .> LINK(PRODUCTS, _['order_items']['product_id'] == _2['id'])
                        .> FILTER(_['products']['is_active'] == 1)
                        .> MAP(RECORD('order_id', _['order_items']['order_id'],
                                      'category_id', _['products']['category_id'],
                                      'line_net', (_['order_items']['unit_price'] * _['order_items']['quantity']) - _['discount']))
                        .> FILTER(_['line_net'] > 10)
                        .> LINK(CATEGORIES, _1['category_id'] == _2['id'])
                         .> BUCKET(_['categories']['name'],
                                   RECORD('category', _K,
                                          'item_lines', COUNT(_),
                                          'total_net', SUM(_, r, r['line_net'])))
                        .> FILTER(_['total_net'] >= 500)
                        .> SORT_BY(_['total_net'], 'DESC')
                        .> TAKE(10)"
         :hybrid-expected nil)

   (list :id "scenario2"
         :name "Scenario 2: Fall-Through Custom SEL Function"
         :desc "Early MAP uses custom SEL function CUSTOM_VIP_SCORE. SQL passes through tier & created_year, filters country='DE', sorts, limits; SEL evaluates score."
         :query "CUSTOMERS .> MAP(RECORD('id', _['id'],
                                         'country', _['country'],
                                         'vip_score', CUSTOM_VIP_SCORE(_['tier'], _['created_year'])))
                           .> FILTER(_['country'] $== 'DE')
                           .> SORT_BY(_['id'], 'ASC')
                           .> TAKE(5)"
         :hybrid-expected t)

   (list :id "scenario3"
         :name "Scenario 3: Mid-Pipeline Memory Fallback"
         :desc "Pushes down join of orders & customers, computes HOST_RISK_SCORE in memory, filters score>50, buckets by country, sorts by count DESC, limits 5."
         :query "ORDERS .> LINK(CUSTOMERS, _['customer_id'] == _2['id'])
                        .> FILTER(_['orders']['status'] $== 'COMPLETED')
                        .> MAP(RECORD('cust_id', _['customers']['id'],
                                      'country', _['customers']['country'],
                                      'discount', _['orders']['discount']))
                        .> MAP(RECORD('cust_id', _['cust_id'],
                                      'country', _['country'],
                                      'score', HOST_RISK_SCORE(_['country'], _['discount'])))
                        .> FILTER(_['score'] > 50)
                        .> BUCKET(_['country'], RECORD('country', _K, 'high_risk_count', COUNT(_)))
                        .> SORT_BY(_['high_risk_count'], 'DESC')
                        .> TAKE(5)"
         :hybrid-expected t)

   (list :id "scenario4"
         :name "Scenario 4: PostgreSQL Spatial GiST Proximity Query"
         :desc "Uses Postgres native location <-> point(...) with GiST index (MariaDB ST_Distance) to find nearest customers to Berlin, distance < 5.0."
         :query "CUSTOMERS .> FILTER(_['dist_berlin'] < 5.0)
                           .> MAP(RECORD('id', _['id'], 'name', _['name'], 'dist_berlin', ROUND(_['dist_berlin'], 6)))
                           .> SORT_BY(_['dist_berlin'], 'ASC')
                           .> TAKE(5)"
         :hybrid-expected nil)

   (list :id "scenario5"
         :name "Scenario 5: 4-Table Equi-Join & Sorting"
         :desc "Joins orders, customers, order_items, and products; filters status, tier, year; projects computed line_total; sorts DESC; limits 9."
         :query "ORDERS .> LINK(CUSTOMERS, _['customer_id'] == _2['id'])
                        .> LINK(ORDER_ITEMS, _['orders']['id'] == _2['order_id'])
                        .> LINK(PRODUCTS, _['order_items']['product_id'] == _2['id'])
                        .> FILTER(_['status'] $== 'COMPLETED' AND _['tier'] $== 'PLATINUM' AND _['orders']['order_year'] == 2026)
                        .> MAP(RECORD('order_id', _['orders']['id'], 'customer', _['customers']['name'], 'sku', _['sku'], 'line_total', _['quantity'] * _['unit_price']))
                        .> SORT_BY(_['line_total'], 'DESC')
                        .> TAKE(9)"
         :hybrid-expected nil)

   (list :id "scenario6"
         :name "Scenario 6: Left Outer Join & Deduplication"
         :desc "Left joins products to order_items, filters unsold products via IS_NULL, maps category_id & status, dedupes with DEDUPE(), sorts deterministically by category_id & status."
         :query "PRODUCTS .> LINK_LEFT(ORDER_ITEMS, _['products']['id'] == _2['product_id'])
                          .> FILTER(IS_NULL(_['order_items']['id']))
                          .> MAP(RECORD('category_id', _['products']['category_id'], 'status', IF(_['products']['is_active'] == 1, 'ACTIVE_UNSOLD', 'INACTIVE_UNSOLD')))
                          .> DEDUPE()
                          .> SORT_BY(_['category_id'] * 2 + IF(_['status'] $== 'ACTIVE_UNSOLD', 0, 1), 'ASC')
                          .> TAKE(10)"
         :hybrid-expected nil)))

(defun sel-value-to-json-ready (v)
  (cond
    ((null v) nil)
    ((sel:value-null-p v) nil)
    ((sel:value-text-p v) (sel:as-text v))
    ((sel:value-bool-p v) (if (sel:as-bool v) t nil))
    ((plusp (sel:value-size v))
     (let ((keys (sel:value-keys v)))
       (if (and keys (every (lambda (k) (every #'digit-char-p k)) keys))
           ;; List
           (mapcar (lambda (k) (sel-value-to-json-ready (sel:value-get v k))) keys)
           ;; Record
           (let ((ht (make-hash-table :test #'equal)))
             (dolist (k keys)
               (let ((child (sel:value-get v k)))
                 (unless (and child (plusp (sel:value-size child))
                              (not (and (sel:value-keys child)
                                        (every (lambda (ck) (every #'digit-char-p ck)) (sel:value-keys child)))))
                   (setf (gethash k ht) (sel-value-to-json-ready child)))))
             ht))))
    (t nil)))

(defun run-benchmarks (&optional (out-path "tools/scale-test/benchmark_results.json"))
  (format *error-output* "Loading dataset ~a...~%" *dataset-file*)
  (let* ((data (with-open-file (in *dataset-file*)
                 (yason:parse in :object-as :alist)))
         (ctx (sel:make-none))
         (bindings-pg (build-schema-bindings "postgresql"))
         (bindings-ma (build-schema-bindings "mariadb")))
    (dolist (pair data)
      (let ((tbl-name (car pair))
            (rows (cdr pair)))
        (sel:value-set ctx (string-upcase tbl-name) (sel:from-native rows))))
    
    ;; Pre-calculate dist_berlin for customers in ctx
    (let ((custs (sel:value-get ctx "CUSTOMERS")))
      (when (and custs (plusp (sel:value-size custs)))
        (dolist (k (sel:value-keys custs))
          (let ((cust (sel:value-get custs k)))
            (let* ((lat (read-from-string (sel:as-text (sel:value-get cust "latitude"))))
                   (lon (read-from-string (sel:as-text (sel:value-get cust "longitude"))))
                   (dx (- lon 13.404954d0))
                   (dy (- lat 52.520008d0))
                   (dist (sqrt (+ (* dx dx) (* dy dy)))))
              (sel:value-set cust "dist_berlin" (sel:make-num (format nil "~,6F" dist))))))))

    (format *error-output* "Dataset loaded into SEL context.~%")

    (let ((results '()))
      (dolist (sc *scenarios*)
        (let* ((id (getf sc :id))
               (name (getf sc :name))
               (desc (getf sc :desc))
               (q (getf sc :query))
               (is-hybrid (getf sc :hybrid-expected))
               (prog (sel:compile-source q))
               ;; Hybrid plan for postgresql and mariadb
               (plan-pg (sel.sql:plan-hybrid prog "postgresql" bindings-pg))
               (plan-ma (sel.sql:plan-hybrid prog "mariadb" bindings-ma))
               (sql-pg (when (sel.sql:hybrid-plan-sql-statement plan-pg)
                         (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan-pg))))
               (sql-ma (when (sel.sql:hybrid-plan-sql-statement plan-ma)
                         (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan-ma))))
               (continuation-pg (sel.sql:hybrid-plan-continuation-program plan-pg)))

          (format *error-output* "Running ~a in memory...~%" id)
          (sb-ext:gc :full t)
          (handler-case
              (let* ((t0 (get-internal-real-time))
                     (mem-res (sel:evaluate q ctx))
                     (t1 (get-internal-real-time))
                     (mem-sec (/ (- t1 t0) (float internal-time-units-per-second)))
                     (rows (sel-value-to-json-ready mem-res)))

                (let ((entry (make-hash-table :test #'equal)))
                  (setf (gethash "id" entry) id
                        (gethash "name" entry) name
                        (gethash "description" entry) desc
                        (gethash "query" entry) q
                        (gethash "is_hybrid" entry) is-hybrid
                        (gethash "sql_postgres" entry) sql-pg
                        (gethash "sql_mariadb" entry) sql-ma
                        (gethash "has_continuation" entry) (not (null continuation-pg))
                        (gethash "in_memory_latency_sec" entry) mem-sec
                        (gethash "in_memory_rows" entry) rows)
                  (push entry results)))
            (error (e)
              (format *error-output* "ERROR in ~a: ~a~%" id e)))))

      (with-open-file (out out-path :direction :output :if-exists :supersede)
        (yason:encode (nreverse results) out))
      (format *error-output* "Benchmark results successfully written to ~a~%" out-path))))

(run-benchmarks)
(sb-ext:exit :code 0)
