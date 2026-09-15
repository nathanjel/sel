;;;; hybrid.lisp - Hybrid execution planner for relational pipelines.
;;;; Slices a pipeline into a maximal SQL pushdown prefix and an in-memory continuation.
;;;;
;;;; The contract every host's planner meets is in docs/SQL-TRANSLATION.md
;;;; §12.1 and is pinned by sql/cases/25-hybrid-plans.sqlt: the planner looks at
;;;; the tree the translator will see, SOURCE-TABLES names PHYSICAL sources (a
;;;; relation's :from, or a relation query's text verbatim), a program stage 1
;;;; refuses is a pure-memory plan rather than an error, and every return path
;;;; fills every slot. This host had no dialect, continuation-ast or
;;;; source-tables slot at all until the cross-language review.

(in-package #:sel.sql)

(defstruct (hybrid-plan (:constructor make-hybrid-plan))
  (dialect nil)
  (sql-statement nil)
  (sql-prefix-ast nil)
  (continuation-ast nil)
  (continuation-program nil)
  (continuation-source-var "_INPUT" :type string)
  (pure-sql-p nil :type boolean)
  (pure-memory-p nil :type boolean)
  (source-tables '() :type list))

(defun hybrid-plan-hybrid-p (plan)
  "Neither pure: a SQL prefix and an in-memory continuation."
  (not (or (hybrid-plan-pure-sql-p plan) (hybrid-plan-pure-memory-p plan))))

(defun physical-source (b)
  "The physical source a relation binding reads: its table, or for a relation
query the query text exactly as the application wrote it."
  (getf (binding-spec b) :from))

(defun source-tables (node bindings)
  "Every physical source NODE reads, first use first, each once. Keyed by the
physical name, so two bindings over one table are one source."
  (let ((out '()))
    (labels ((walk (n)
               (when (and n (sel::node-p n))
                 (if (and (eq (sel::node-kind n) :var)
                          (bindings-has bindings (sel::node-s n)))
                     (let ((b (bindings-get bindings (sel::node-s n) (sel::node-pos n))))
                       (when (eq (binding-kind b) :relation)
                         (let ((table (physical-source b)))
                           (unless (member table out :test #'equal)
                             (push table out)))))
                     (progn
                       (walk (sel::node-l n))
                       (walk (sel::node-r n))
                       (dolist (item (sel::node-items n)) (walk item)))))))
      (walk node))
    (nreverse out)))

(defun pure-memory-plan (program dialect bindings)
  "The plan for a program nothing of which reaches the database. The
continuation is the program itself, and the AST it exposes is the program's
own, so a caller sees the same tree whichever way the plan went."
  (make-hybrid-plan :dialect dialect
                    :pure-memory-p t
                    :continuation-program program
                    :continuation-ast (sel:program-ast program)
                    :source-tables (source-tables (sel:program-ast program) bindings)))

(defun plan-hybrid (program dialect &optional bindings options)
  "Analyzes PROGRAM and splits it into a maximal SQL pushdown prefix and an in-memory continuation.
Returns a HYBRID-PLAN struct. OPTIONS is a plist; :strict reaches the translator."
  (require-target dialect)
  (let* ((b (make-bindings (or bindings '())))
         (tr (%translator dialect b (and (getf options :strict) t)))
         (bs (translator-bindings tr)))
    (bindings-check-aliases bs)
    (multiple-value-bind (names root) (const-scope bs)
      (setf (translator-const-names tr) names
            (translator-const-root tr) root)
      ;; Stage 1 first, exactly as the translator runs it, so the tree unwound
      ;; below is the one a prefix will be translated from. A program stage 1
      ;; refuses -- `A += 1; ...`, a bare statement before the result -- is a
      ;; program no part of which can be pushed down, which is a pure-memory
      ;; plan and not an error: "none of it" is one of the planner's answers.
      (let ((norm (handler-case (normalise (sel:program-ast program) names root)
                    (sql-error () (return-from plan-hybrid (pure-memory-plan program dialect bs))))))
        (when (clist-p norm)
          (return-from plan-hybrid (pure-memory-plan program dialect bs)))
        (multiple-value-bind (curr steps) (sel::unwind-pipeline (sel:optimize-ast-logical norm))
          ;; No steps, or a source that is not a bound relation: nothing to push.
          (unless (and steps curr (not (clist-p curr)) (eq (snode-kind curr) :var)
                       (bindings-has bs (sel::node-s curr))
                       (eq (binding-kind (bindings-get bs (sel::node-s curr) (snode-pos curr)))
                           :relation))
            (return-from plan-hybrid (pure-memory-plan program dialect bs)))
          (let ((n-steps (length steps))
                (source-node curr)
                (input-var "_INPUT"))
            ;; 1. The whole pipeline, unless its rows would be a bucket's keys: the
            ;; translator renders a bare bucket as its keys, and a plan that
            ;; pushes the whole of `... .> BUCKET(k)` would hand them back.
            (let* ((full-ast (sel::build-pipeline-ast source-node steps))
                   (full-prog (sel::%make-program "" full-ast))
                   (full-frag (and (not (bucket-rows-are-keys-p steps))
                                   (try-translate-statement full-prog dialect bindings options))))
              (when full-frag
                (return-from plan-hybrid
                  (make-hybrid-plan :dialect dialect
                                    :sql-statement full-frag
                                    :sql-prefix-ast full-ast
                                    :pure-sql-p t
                                    :source-tables (source-tables full-ast bs)))))
            ;; 2. The MAP fall-through.
            (let ((ft-plan (try-plan-fallthrough source-node steps dialect bindings options input-var)))
              (when ft-plan
                (setf (hybrid-plan-dialect ft-plan) dialect
                      (hybrid-plan-source-tables ft-plan)
                      (source-tables (hybrid-plan-sql-prefix-ast ft-plan) bs))
                (return-from plan-hybrid ft-plan)))
            ;; 3. The longest translatable prefix, with the rest in memory.
            (loop for k from (1- n-steps) downto 1 do
              (let* ((prefix-steps (subseq steps 0 k))
                     (prefix-ast (sel::build-pipeline-ast source-node prefix-steps))
                     (prefix-prog (sel::%make-program "" prefix-ast))
                     (frag (and (not (bucket-rows-are-keys-p prefix-steps))
                                (try-translate-statement prefix-prog dialect bindings options))))
                (when frag
                  (let* ((rem-steps (subseq steps k))
                         (cont-root (let ((v (sel::make-node :var (sel::node-pos (first rem-steps)))))
                                      (setf (sel::node-s v) input-var)
                                      v))
                         (cont-ast (sel::build-pipeline-ast cont-root rem-steps))
                         (cont-prog (sel::%make-program "" cont-ast)))
                    (return-from plan-hybrid
                      (make-hybrid-plan :dialect dialect
                                        :sql-statement frag
                                        :sql-prefix-ast prefix-ast
                                        :continuation-ast cont-ast
                                        :continuation-program cont-prog
                                        :continuation-source-var input-var
                                        :source-tables (source-tables prefix-ast bs)))))))
            ;; 4. Nothing pushes down.
            (pure-memory-plan program dialect bs)))))))

(defun bucket-rows-are-keys-p (steps)
  "Whether the SQL rows for STEPS are a bucket's KEYS rather than the value SEL
would have produced. A BUCKET without a projection is open: the translator
projects its keys, and SEL's value is a map of member rows. The next MAP closes
it -- it becomes the bucket's projection, one statement, one value in both
lanes -- and a FILTER between them is a HAVING. Any other step seals it: the
members are gone, and no continuation can get them back. So a prefix that is
open or sealed is not a split point, whatever the translator says about it,
and the MAP fall-through must not fire on a MAP that closes one -- its custom
half would be evaluated over key rows."
  (let ((open nil))
    (dolist (step steps open)
      (let ((name (sel::node-s step)))
        (cond ((equal name "BUCKET")
               (when open (return t))
               (setf open (= (length (sel::node-items step)) 2)))
              ((and open (equal name "MAP")) (setf open nil))
              ((and open (not (equal name "FILTER"))) (return t)))))))

(defun walk-node-children (n fn)
  "Calls FN on every child node of N -- l, r and items -- whatever its kind."
  (when (and n (sel::node-p n))
    (let ((l (sel::node-l n)) (r (sel::node-r n)))
      (when (and l (sel::node-p l)) (funcall fn l))
      (when (and r (sel::node-p r)) (funcall fn r))
      (dolist (item (sel::node-items n))
        (when (and item (sel::node-p item)) (funcall fn item))))))

(defun binder-name-p (name binder)
  "Whether NAME binds the row: BINDER, or one of the implicit names. A null
BINDER means any name does -- a downstream step binds the row however it likes."
  (or (null binder)
      (string-equal name binder) (string-equal name "_")
      (string-equal name "_1") (string-equal name "_2")))

(defun field-read-p (n)
  "Whether N is BINDER['field']: an index whose object is a var and whose key is text."
  (and n (sel::node-p n) (eq (sel::node-kind n) :index)
       (let ((l (sel::node-l n)) (r (sel::node-r n)))
         (and l (sel::node-p l) (eq (sel::node-kind l) :var)
              r (sel::node-p r) (eq (sel::node-kind r) :text)))))

(defun collect-field-references (node &optional (binder "_"))
  "Collects all field names accessed via BINDER['field'] in NODE, first seen
first and compared exactly: SEL's record keys are case-sensitive, so name and
Name are two fields. A null BINDER counts a read under any name."
  (let ((refs '()))
    (labels ((walk (n)
               (when (and n (sel::node-p n))
                 (when (and (field-read-p n) (binder-name-p (sel::node-s (sel::node-l n)) binder))
                   (pushnew (sel::node-s (sel::node-r n)) refs :test #'string=))
                 (walk-node-children n #'walk))))
      (walk node))
    (nreverse refs)))

(defun collect-all-step-field-references (steps)
  (let ((all-refs '()))
    (dolist (step steps)
      (dolist (ref (collect-field-references step))
        (pushnew ref all-refs :test #'string-equal)))
    all-refs))

(defparameter +sql-special-calls+
  '("IF" "COND" "COALESCE" "COUNT" "SUM" "AVG" "MIN" "MAX" "RECORD" "LIST"))

(defun contains-unsupported-sql-p (node dialect)
  "Returns T if NODE contains any function call not supported by DIALECT."
  (let ((unsupported nil))
    (labels ((walk (n)
               (when (and n (sel::node-p n) (not unsupported))
                 (when (eq (sel::node-kind n) :call)
                   (let ((name (sel::node-s n)))
                     (unless (member name +sql-special-calls+ :test #'equal)
                       (multiple-value-bind (entry found) (dialect-entry dialect :funcs name)
                         (when (or (not found) (null entry) (stringp entry))
                           (setf unsupported t))))))
                 ;; Every child, whatever the kind: a list literal holds calls too.
                 (walk-node-children n #'walk))))
      (walk node))
    unsupported))

;; The steps the MAP fall-through may push past the MAP. Each keeps the rows as
;; they are -- the same records, fewer or reordered -- so the custom half of the
;; projection still runs over its own input. A step that changes the row shape
;; (MAP, SELECT_COLS, LINK, BUCKET) would put it over something else, and the
;; whole-row comparisons (DEDUPE, DISTINCT, the keyless sorts) would compare the
;; dependency columns SQL carries where SEL compares the custom values.
(defparameter +fallthrough-downstream+ '("FILTER" "SORT_BY" "TOP_BY" "TAKE" "DROP"))

(defun reads-whole-row-p (node binder)
  "Whether NODE reads the row itself -- the binder outside an index with a text
key, as in GET(_, \"name\") or COUNT(_) -- which no projected column can stand in for."
  (labels ((walk (n)
             (when (and n (sel::node-p n))
               (cond ((eq (sel::node-kind n) :var) (binder-name-p (sel::node-s n) binder))
                     ;; A field read; the object is not a whole-row read.
                     ((field-read-p n) (walk (sel::node-r n)))
                     (t (let ((found nil))
                          (walk-node-children n (lambda (c) (when (walk c) (setf found t))))
                          found))))))
    (walk node)))

(defun own-field-read-p (key-node value-node binder)
  "Whether a pushable pair is the plain field read BINDER[key] of its own key,
so that a dependency of the same name may share its column."
  (and (field-read-p value-node)
       (string-equal (sel::node-s (sel::node-l value-node)) binder)
       (string= (sel::node-s (sel::node-r value-node)) (sel::node-s key-node))))

(defun make-field-read (binder field pos)
  "The node BINDER[field] at POS."
  (let ((idx (sel::make-node :index pos))
        (var (sel::make-node :var pos))
        (txt (sel::make-node :text pos)))
    (setf (sel::node-s var) binder
          (sel::node-s txt) field
          (sel::node-l idx) var
          (sel::node-r idx) txt)
    idx))

(defun try-plan-fallthrough (source-node steps dialect bindings options input-var)
  "Detects an early MAP whose RECORD mixes translatable pairs with custom ones.
Rewrites the SQL prefix to project the translatable pairs plus the columns the
custom pairs depend on, pushes the remaining pipeline to SQL, and evaluates the
custom pairs on the rows that come back."
  (let ((map-idx (position "MAP" steps :key (lambda (s) (sel::node-s s)) :test #'equal)))
    (unless map-idx (return-from try-plan-fallthrough nil))
    (when (bucket-rows-are-keys-p (subseq steps 0 map-idx))
      (return-from try-plan-fallthrough nil))
    (let* ((map-step (nth map-idx steps))
           (args (sel::node-items map-step))
           (explicit (and (= (length args) 3)
                          (sel::node-p (second args))
                          (eq (sel::node-kind (second args)) :var)
                          (not (sel::node-grouped (second args)))))
           (binder (if explicit (sel::node-s (second args)) "_"))
           (rec-node (cond (explicit (third args))
                           ((= (length args) 2) (second args))
                           (t nil))))
      (unless (and rec-node (sel::node-p rec-node)
                   (eq (sel::node-kind rec-node) :call)
                   (member (sel::node-s rec-node) '("RECORD" "LAZY_RECORD") :test #'equal)
                   (evenp (length (sel::node-items rec-node)))
                   (loop for (k nil) on (sel::node-items rec-node) by #'cddr
                         always (and (sel::node-p k) (eq (sel::node-kind k) :text))))
        (return-from try-plan-fallthrough nil))
      ;; Each pair is (key-node value-node pushable-p).
      (let* ((pairs (loop for (k v) on (sel::node-items rec-node) by #'cddr
                          collect (list k v (not (contains-unsupported-sql-p v dialect)))))
             (pushable (remove-if-not #'third pairs))
             (custom (remove-if #'third pairs)))
        (when (or (null custom) (null pushable))
          (return-from try-plan-fallthrough nil))
        ;; The custom half runs over the rows the SQL returns; a read of the row
        ;; itself cannot be served by any column.
        (when (some (lambda (p) (reads-whole-row-p (second p) binder)) custom)
          (return-from try-plan-fallthrough nil))
        ;; Every step after the MAP goes into the SQL, so each must keep the rows
        ;; as they are, and may read only what SEL's rows have after the MAP: the
        ;; pushable keys. The custom keys are not in the SQL; a dependency column
        ;; is in the SQL but not in SEL's row.
        (let ((downstream (subseq steps (1+ map-idx)))
              (projected (mapcar (lambda (p) (sel::node-s (first p))) pushable)))
          (dolist (step downstream)
            (unless (member (sel::node-s step) +fallthrough-downstream+ :test #'equal)
              (return-from try-plan-fallthrough nil))
            ;; items[0] is the step's input -- the pipeline so far -- not its
            ;; own text; the step binds the row under a name of its own, so any
            ;; read counts.
            (dolist (arg (rest (sel::node-items step)))
              (dolist (field (collect-field-references arg nil))
                (unless (member field projected :test #'string=)
                  (return-from try-plan-fallthrough nil)))))
          ;; A dependency may share a projected column only when that column IS
          ;; the field: "customer_id", _["amount"] projects amount under the name
          ;; the custom half would read customer_id by. Names are compared
          ;; exactly, as SEL compares them; and a dependency that differs from a
          ;; projected key only by case is not projected beside it, because SQL
          ;; aliases are not case-sensitive everywhere.
          (let ((own (loop for p in pushable
                           when (own-field-read-p (first p) (second p) binder)
                             collect (sel::node-s (first p))))
                (dependencies '()))
            ;; "Case" here is ASCII case, as everywhere in SEL -- never the host's.
            (flet ((same-folded (a b) (string= (sel::ascii-upcase a) (sel::ascii-upcase b))))
              (dolist (p custom)
                (dolist (field (collect-field-references (second p) binder))
                  (cond ((member field projected :test #'string=)
                         (unless (member field own :test #'string=)
                           (return-from try-plan-fallthrough nil)))
                        ((member field projected :test #'same-folded)
                         (return-from try-plan-fallthrough nil))
                        ((not (member field dependencies :test #'string=))
                         ;; Two dependencies must not differ only by case either.
                         (when (member field dependencies :test #'same-folded)
                           (return-from try-plan-fallthrough nil))
                         (push field dependencies))))))
            (setf dependencies (nreverse dependencies))
            ;; The rewritten RECORD for SQL: the pushable pairs, then a column
            ;; per dependency.
            (let ((new-items '()))
              (dolist (p pushable)
                (push (first p) new-items)
                (push (second p) new-items))
              (dolist (d dependencies)
                (let ((k (sel::make-node :text (sel::node-pos map-step))))
                  (setf (sel::node-s k) d)
                  (push k new-items)
                  (push (make-field-read binder d (sel::node-pos map-step)) new-items)))
              (let* ((rewritten-rec (sel::copy-node rec-node))
                     (rewritten-map (sel::copy-node map-step)))
                (setf (sel::node-items rewritten-rec) (nreverse new-items))
                (setf (sel::node-items rewritten-map)
                      (if explicit
                          (list (first args) (second args) rewritten-rec)
                          (list (first args) rewritten-rec)))
                (let* ((rewritten-steps (append (subseq steps 0 map-idx)
                                                (list rewritten-map)
                                                downstream))
                       (rewritten-ast (sel::build-pipeline-ast source-node rewritten-steps))
                       (rewritten-prog (sel::%make-program "" rewritten-ast))
                       (sql-frag (try-translate-statement rewritten-prog dialect bindings options)))
                  (unless sql-frag (return-from try-plan-fallthrough nil))
                  ;; The continuation re-applies the projection to the rows that
                  ;; come back: a pushable pair is passed through BY KEY -- the SQL
                  ;; already computed it, under that name -- and a custom pair is
                  ;; evaluated as written, over the dependency columns projected
                  ;; beside it.
                  (let ((cont-items '()))
                    (dolist (p pairs)
                      (push (first p) cont-items)
                      (push (if (third p)
                                (make-field-read binder (sel::node-s (first p))
                                                 (sel::node-pos (second p)))
                                (second p))
                            cont-items))
                    (let* ((cont-rec (sel::copy-node rec-node))
                           (cont-root (sel::make-node :var (sel::node-pos map-step)))
                           (cont-map (sel::copy-node map-step)))
                      (setf (sel::node-items cont-rec) (nreverse cont-items))
                      (setf (sel::node-s cont-root) input-var)
                      (setf (sel::node-items cont-map)
                            (if explicit
                                (list cont-root (second args) cont-rec)
                                (list cont-root cont-rec)))
                      ;; The caller fills dialect and source-tables.
                      (make-hybrid-plan
                       :sql-statement sql-frag
                       :sql-prefix-ast rewritten-ast
                       :continuation-ast cont-map
                       :continuation-program (sel::%make-program "" cont-map)
                       :continuation-source-var input-var
                       :pure-sql-p nil
                       :pure-memory-p nil))))))))))))

(defun execute-hybrid (plan db-runner &optional context)
  "Execute a HYBRID-PLAN using DB-RUNNER for SQL execution and SEL:RUN for in-memory continuation.
DB-RUNNER is a function (lambda (sql-string params) ...) that returns a SEL:VALUE (e.g. list of rows)."
  (cond
    ((hybrid-plan-pure-sql-p plan)
     (let ((frag (hybrid-plan-sql-statement plan)))
       (funcall db-runner (as-statement frag) (fragment-params frag))))
    ((hybrid-plan-pure-memory-p plan)
     (sel:run (hybrid-plan-continuation-program plan) context))
    (t
     ;; Hybrid execution: DB first, then in-memory continuation
     (let* ((frag (hybrid-plan-sql-statement plan))
            (db-rows (funcall db-runner (as-statement frag) (fragment-params frag)))
            (cont-prog (hybrid-plan-continuation-program plan))
            (input-var (hybrid-plan-continuation-source-var plan)))
       (let ((cont-context (sel:make-none)))
         (when context
           (let ((c-val (if (sel:value-p context) context (sel:from-native context))))
             (dolist (k (sel:value-keys c-val))
               (sel:value-set cont-context k (sel:value-get c-val k)))))
         (sel:value-set cont-context input-var (if (sel:value-p db-rows) db-rows (sel:from-native db-rows)))
         (sel:run cont-prog cont-context))))))
