;;;; hybrid.lisp - Hybrid execution planner for relational pipelines.
;;;; Slices a pipeline into a maximal SQL pushdown prefix and an in-memory continuation.
;;;;
;;;; The contract every host's planner meets is in docs/SQL-TRANSLATION.md
;;;; §12.1 and is pinned by sql/cases/25-hybrid-plans.sqlt: the planner looks at
;;;; the PIPELINE, whichever helper assignments it is written through, and then
;;;; at the logical optimiser's rewrite of that (unwinding the raw AST first
;;;; classified `X = ORDERS; X .> TAKE(1)` as pure memory, because a `seq` is
;;;; not a pipeline; inlining every helper the way stage 1 does for translate
;;;; made the continuation report an error at the helper's definition where
;;;; run reports its use -- see "helper assignments" below for what is done
;;;; instead), SOURCE-TABLES names PHYSICAL sources (a relation's :from, or a
;;;; relation query's text verbatim), a program stage 1 refuses is a
;;;; pure-memory plan rather than an error, and every return path fills every
;;;; slot. This host had no dialect, continuation-ast or source-tables slot at
;;;; all until the cross-language review.

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
  (source-tables '() :type list)
  (selected-member nil)) ; plist :partition-key/:revision-key; full source rows

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

;;; --- helper assignments ----------------------------------------------------
;;;
;;; Stage 1 inlines a helper assignment for translate: `Y = "x"; ... + Y` is
;;; rendered as `... + "x"`, the literal keeping its definition-site position,
;;; which is right for a refusal message. It is wrong for the memory half of a
;;; plan, because that half is a program run evaluates and §12.1 promises it
;;; reports errors where run would: run evaluates the READ of Y at the use
;;; site and reports `+`'s operand there, and it evaluates the definition once,
;;; before the pipeline, not once per row (review 2026-09-15 finding AJ). So
;;; the planner does not inline. It plans the program as written, three ways:
;;;
;;;   * A helper that IS a literal -- after inlining earlier such helpers and
;;;     folding, `N = 1 + 1` as much as `N = 2` -- is inlined at its reads,
;;;     stamped with the read's position. That is invisible: a leaf literal
;;;     cannot fail, and neither can the read, since the definition exists. It
;;;     keeps `TAKE(N)` a `LIMIT 2` rather than a helper the SQL has to carry.
;;;   * A helper read as the pipeline's SOURCE is unwound through: `X = ORDERS
;;;     .> TAKE(2); X .> MAP(...)` is one pipeline over ORDERS, so the prefix
;;;     search sees every step. Only the source position looks through a
;;;     helper; a read anywhere else stays a read.
;;;   * What is handed to the translator, and what is kept for the
;;;     continuation, carries in front of it the assignments it still reads and
;;;     the ones those read, in program order, as the program wrote them. The
;;;     translator runs its own stage 1 over that seq and inlines; the
;;;     continuation evaluates them once, before its steps, as run does. An
;;;     assignment nothing after the split reads is dropped, as stage 1 drops
;;;     it for translate -- the one departure, and the same one.

(defparameter +literal-kinds+ '(:num :text :bool :null))

(defun literal-kind-p (node)
  (and node (sel::node-p node) (member (sel::node-kind node) +literal-kinds+) t))

(defun statements (ast)
  "The leading statements and the result expression of a program, as two values."
  (if (eq (sel::node-kind ast) :seq)
      (let ((items (sel::node-items ast)))
        (values (butlast items) (car (last items))))
      (values '() ast)))

(defun assigned-name (statement)
  "The name a leading statement assigns. Stage 1 has accepted every statement
by the time this runs, so each is an assignment whose target is a name, or a
name indexed by constants."
  (let ((target (sel::node-l statement)))
    (loop while (eq (sel::node-kind target) :index)
          do (setf target (sel::node-l target)))
    (sel::node-s target)))

(defun definitions (leading)
  "The whole-name definitions, an alist of name to value node. Stage 1 refuses
a name assigned twice, or both whole and by index, so each name here has
exactly one."
  (let ((defs '()))
    (dolist (s leading (nreverse defs))
      (when (eq (sel::node-kind (sel::node-l s)) :var)
        (push (cons (sel::node-s (sel::node-l s)) (sel::node-r s)) defs)))))

(defun inline-literals (node literals &optional bound)
  "NODE with every read of a literal helper replaced by the literal, stamped
with the read's position. Binder scoping is stage 1's: a binder shadows a
same-named helper inside its body. Copies on the way down, never writes."
  (unless node (return-from inline-literals node))
  (flet ((inline-child (child &optional (scope bound))
           (inline-literals child literals scope)))
    (case (sel::node-kind node)
      (:var
       (let ((cell (and (not (member (sel::node-s node) bound :test #'equal))
                        (assoc (sel::node-s node) literals :test #'equal))))
         (if cell
             (let ((c (sel::copy-node (cdr cell))))
               (setf (sel::node-pos c) (sel::node-pos node))
               c)
             node)))
      ((:num :text :bool :null) node)
      (:un (let ((c (sel::copy-node node)))
             (setf (sel::node-l c) (inline-child (sel::node-l node)))
             c))
      ((:bin :index)
       (let ((c (sel::copy-node node)))
         (setf (sel::node-l c) (inline-child (sel::node-l node))
               (sel::node-r c) (inline-child (sel::node-r node)))
         c))
      ((:list :seq)
       (let ((c (sel::copy-node node)))
         (setf (sel::node-items c) (mapcar #'inline-child (sel::node-items node)))
         c))
      (:assign (let ((c (sel::copy-node node)))
                 (setf (sel::node-r c) (inline-child (sel::node-r node)))
                 c))
      (:call
       (let* ((args (sel::node-items node))
              (spec (sel::node-spec node))
              (binds (and spec (sel::spec-binds spec)))
              (inner (copy-list bound)))
         (when binds
           (push "_K" inner)
           (push (if (and (= (length args) 3) (is-binder-name (second args)))
                     (sel::node-s (second args))
                     "_")
                 inner))
         (let ((c (sel::copy-node node)))
           (setf (sel::node-items c)
                 (loop for arg in args
                       for i from 0
                       collect (if (and binds (= i 1) (= (length args) 3) (is-binder-name arg))
                                   arg
                                   (inline-child arg (if (= i 0) bound inner)))))
           c)))
      (t node))))

(defun literal-helpers (leading)
  "The literal helpers, an alist of name to literal: each whole-name definition,
after the earlier literal helpers are inlined into it and it is folded, when
what is left is a leaf."
  (let ((literals '()))
    (dolist (s leading literals)
      (when (eq (sel::node-kind (sel::node-l s)) :var)
        (let ((folded (sel:optimize-ast-logical (inline-literals (sel::node-r s) literals))))
          (when (literal-kind-p folded)
            (push (cons (sel::node-s (sel::node-l s)) folded) literals)))))))

(defun unwind-through-helpers (result defs literals)
  "The pipeline the planner probes, as two values (source, steps): the result
unwound, and where its source is a helper, that helper's definition unwound in
turn."
  (multiple-value-bind (source steps) (sel::unwind-pipeline (inline-literals result literals))
    (let ((seen '()))
      (loop while (and source (sel::node-p source) (eq (sel::node-kind source) :var)
                       (assoc (sel::node-s source) defs :test #'equal)
                       (not (member (sel::node-s source) seen :test #'equal)))
            do (push (sel::node-s source) seen)
               (multiple-value-bind (inner-source inner-steps)
                   (sel::unwind-pipeline
                    (inline-literals (cdr (assoc (sel::node-s source) defs :test #'equal)) literals))
                 (setf source inner-source
                       steps (append inner-steps steps)))))
    (values source steps)))

(defun read-names (node)
  "The names a tree reads, binders included: an over-approximation that can
only keep an assignment the tree does not need, never drop one it does."
  (let ((out '()))
    (labels ((walk (n)
               (when (and n (sel::node-p n))
                 (if (eq (sel::node-kind n) :var)
                     (pushnew (sel::node-s n) out :test #'equal)
                     (walk-node-children n #'walk)))))
      (walk node))
    out))

(defun referenced-assignments (leading node)
  "The leading assignments NODE depends on, in program order: those whose name
it reads, and those THEY read, transitively."
  (let ((needed (read-names node))
        (grew t))
    (loop while grew
          do (setf grew nil)
             (dolist (s leading)
               (when (member (assigned-name s) needed :test #'equal)
                 (dolist (name (read-names (sel::node-r s)))
                   (unless (member name needed :test #'equal)
                     (push name needed)
                     (setf grew t))))))
    (remove-if-not (lambda (s) (member (assigned-name s) needed :test #'equal)) leading)))

(defun with-helpers (leading node)
  "NODE behind the assignments it depends on, as the program wrote them -- a
seq the translator's stage 1 inlines and the evaluator runs in order -- or
NODE itself when it depends on none."
  (let ((kept (referenced-assignments leading node)))
    (if kept
        (let ((seq (sel::make-node :seq (sel::node-pos (first kept)))))
          (setf (sel::node-items seq) (append kept (list node)))
          seq)
        node)))

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
         (bs (translator-bindings tr))
         (identity-barrier nil))
    (bindings-check-aliases bs)
    (multiple-value-bind (names root) (const-scope bs)
      (setf (translator-const-names tr) names
            (translator-const-root tr) root)
      ;; Stage 1 first, exactly as the translator runs it, for its verdict. A
      ;; program stage 1 refuses -- `A += 1; ...`, a bare statement before the
      ;; result -- is a program no part of which can be pushed down, which is a
      ;; pure-memory plan and not an error: "none of it" is one of the
      ;; planner's answers. Its TREE is not what is planned, though: see
      ;; "helper assignments" below.
      (let ((norm (handler-case (normalise (sel:program-ast program) names root)
                    (sql-error () (return-from plan-hybrid (pure-memory-plan program dialect bs))))))
        (when (clist-p norm)
          (return-from plan-hybrid (pure-memory-plan program dialect bs)))
        (setf identity-barrier (identity-loss-before-grouping-p norm)))
      (multiple-value-bind (leading result) (statements (sel:program-ast program))
        (let ((literals (literal-helpers leading))
              (defs (definitions leading)))
          (flet ((relation-p (node)
                   (and node (sel::node-p node) (eq (sel::node-kind node) :var)
                        (bindings-has bs (sel::node-s node))
                        (eq (binding-kind (bindings-get bs (sel::node-s node) (sel::node-pos node)))
                            :relation)))
                 (wrap (node) (with-helpers leading node))
                 ;; The physical sources of a wrapped tree are read off what the
                 ;; translator renders: stage 1's tree, where an assignment a
                 ;; binder shadows is gone.
                 (tables (wrapped) (source-tables (normalise wrapped names root) bs)))
            (multiple-value-bind (unwound-source unwound-steps)
                (unwind-through-helpers result defs literals)
              ;; No steps, or a source that is not a bound relation: nothing to push.
              (unless (and unwound-steps (relation-p unwound-source))
                (return-from plan-hybrid (pure-memory-plan program dialect bs)))
              (multiple-value-bind (source-node steps)
                  (sel::unwind-pipeline
                   (sel:optimize-ast-logical (sel::build-pipeline-ast unwound-source unwound-steps)))
                (unless (and steps (relation-p source-node))
                  (return-from plan-hybrid (pure-memory-plan program dialect bs)))
                (let ((n-steps (length steps))
                      (input-var "_INPUT"))
                  ;; 1. The whole pipeline, unless its rows would be a bucket's keys or
                  ;; a join's rows without their binders: the translator renders a
                  ;; bare bucket as its keys, and a plan that pushes the whole of
                  ;; `... .> BUCKET(k)` would hand them back.
                  (let* ((full-ast (wrap (sel::build-pipeline-ast source-node steps)))
                         (full-prog (sel::%make-program "" full-ast))
                         (full-frag (and (not identity-barrier) (not (rows-are-not-the-value-p steps))
                                         (try-translate-statement full-prog dialect bindings options))))
                    (when full-frag
                      (return-from plan-hybrid
                        (make-hybrid-plan :dialect dialect
                                          :sql-statement full-frag
                                          :sql-prefix-ast full-ast
                                          :pure-sql-p t
                                          :source-tables (tables full-ast)))))
                  (let ((latest (try-plan-latest-member source-node steps dialect bs options #'wrap)))
                    (when latest (return-from plan-hybrid latest)))
                  ;; 2. The MAP fall-through.
                  (let ((ft-plan (and (not identity-barrier)
                                     (try-plan-fallthrough source-node steps dialect bindings options
                                                       input-var defs #'wrap))))
                    (when ft-plan
                      (setf (hybrid-plan-dialect ft-plan) dialect
                            (hybrid-plan-source-tables ft-plan)
                            (tables (hybrid-plan-sql-prefix-ast ft-plan)))
                      (return-from plan-hybrid ft-plan)))
                  ;; 3. The longest translatable prefix, with the rest in memory.
                  (loop for k from (1- n-steps) downto 1 do
                    (let* ((prefix-steps (subseq steps 0 k))
                           (prefix-ast (wrap (sel::build-pipeline-ast source-node prefix-steps)))
                           (prefix-prog (sel::%make-program "" prefix-ast))
                           (frag (and (not (rows-are-not-the-value-p prefix-steps))
                                      (or (not identity-barrier)
                                          (handler-case
                                              (not (identity-loss-before-grouping-p (normalise prefix-ast names root) t))
                                            (sql-error () nil)))
                                      (try-translate-statement prefix-prog dialect bindings options))))
                      (when frag
                        (let* ((rem-steps (subseq steps k))
                               (cont-root (let ((v (sel::make-node :var (sel::node-pos (first rem-steps)))))
                                            (setf (sel::node-s v) input-var)
                                            v))
                               (cont-ast (wrap (sel::build-pipeline-ast cont-root rem-steps)))
                               (cont-prog (sel::%make-program "" cont-ast)))
                          (return-from plan-hybrid
                            (make-hybrid-plan :dialect dialect
                                              :sql-statement frag
                                              :sql-prefix-ast prefix-ast
                                              :continuation-ast cont-ast
                                              :continuation-program cont-prog
                                              :continuation-source-var input-var
                                              :source-tables (tables prefix-ast)))))))
                  ;; 4. Nothing pushes down.
                  (pure-memory-plan program dialect bs))))))))))

(defun latest-field-name (n)
  "A direct default-row field; no computed/coerced partition or revision."
  (when (and n (sel::node-p n) (eq (sel::node-kind n) :index)
             (eq (sel::node-kind (sel::node-l n)) :var)
             (equal (sel::node-s (sel::node-l n)) "_")
             (eq (sel::node-kind (sel::node-r n)) :text))
    (sel::node-s (sel::node-r n))))

(defun try-plan-latest-member (source steps dialect bindings options wrap)
  "Aggregate/join-back for a unique descending TOP 1, with local reshaping."
  (handler-case
      (block candidate
        (unless (member dialect '("mariadb" "mysql" "postgresql" "sqlite") :test #'equal)
          (return-from candidate nil))
        (let* ((rel (binding-spec (bindings-get bindings (sel::node-s source))))
               (revision (getf rel :unique-key))
               (at (position "BUCKET" steps :key #'sel::node-s :test #'equal)))
          (unless (and revision at (not (getf rel :from-raw-p)) (not (getf rel :correlate)))
            (return-from candidate nil))
          (let* ((bucket (nth at steps)) (ba (sel::node-items bucket))
                 (partition (and (member (length ba) '(2 3)) (latest-field-name (second ba))))
                 (body (if (= (length ba) 3) (third ba)
                           (let ((m (nth (1+ at) steps)))
                             (when (and m (equal (sel::node-s m) "MAP") (= (length (sel::node-items m)) 2))
                               (second (sel::node-items m))))))
                 (fields (getf rel :fields))
                 (pf (cdr (assoc (sel::ascii-upcase (or partition "")) fields :test #'equal)))
                 (rf (cdr (assoc (sel::ascii-upcase revision) fields :test #'equal))))
            (unless (and partition body (eq (sel::node-kind body) :call)
                         (equal (sel::node-s body) "RECORD") (= (length (sel::node-items body)) 4)
                         (member (getf pf :type) '(:num :text)) (eq (getf rf :type) :num)
                         (equal (getf pf :column) partition) (equal (getf rf :column) revision)
                         (not (getf pf :raw)) (not (getf rf :raw)) (not (getf rf :guard)))
              (return-from candidate nil))
            (let* ((ra (sel::node-items body)) (values (list (second ra) (fourth ra)))
                   (top (find-if (lambda (n) (and (eq (sel::node-kind n) :call) (equal (sel::node-s n) "TOP_BY"))) values))
                   (key (find-if (lambda (n) (and (eq (sel::node-kind n) :var) (equal (sel::node-s n) "_K"))) values))
                   (ta (and top (sel::node-items top))))
              (unless (and (every (lambda (n) (eq (sel::node-kind n) :text)) (list (first ra) (third ra)))
                           (not (equal (sel::node-s (first ra)) (sel::node-s (third ra))))
                           key (= (length ta) 4) (eq (sel::node-kind (first ta)) :var)
                           (equal (sel::node-s (first ta)) "_") (equal (latest-field-name (second ta)) revision)
                           (eq (sel::node-kind (third ta)) :text) (equal (sel::node-s (third ta)) "DESC")
                           (eq (sel::node-kind (fourth ta)) :num) (equal (sel::node-s (fourth ta)) "1"))
                (return-from candidate nil)))
            (dolist (step (subseq steps 0 at))
              (unless (or (equal (sel::node-s step) "FILTER")
                          (and (equal (sel::node-s step) "SORT_BY")
                               (member (length (sel::node-items step)) '(2 3))
                               (equal (latest-field-name (second (sel::node-items step))) revision)
                               (or (= (length (sel::node-items step)) 2)
                                   (and (eq (sel::node-kind (third (sel::node-items step))) :text)
                                        (equal (sel::node-s (third (sel::node-items step))) "ASC")))))
                (return-from candidate nil)))
            (let* ((input-steps (subseq steps 0 at))
                   (dummy (sel:program-ast (sel:compile-source "_INPUT .> FILTER(TRUE)")))
                   (prefix (funcall wrap (sel::build-pipeline-ast source (or input-steps (list dummy)))))
                   (sql (try-translate-statement (sel::%make-program "" prefix) dialect (binding-map-sorted bindings) options)))
              (unless sql (return-from candidate nil))
              (let ((input "_sel_input") (groups "_sel_latest"))
                (loop while (equal (sel::ascii-upcase input) (sel::ascii-upcase (getf rel :from))) do (setf input (concatenate 'string input "_")))
                (loop while (or (equal (sel::ascii-upcase groups) (sel::ascii-upcase (getf rel :from))) (equal groups input)) do (setf groups (concatenate 'string groups "_")))
                (let* ((qi (emit-ident dialect input)) (qg (emit-ident dialect groups))
                       (qr (emit-ident dialect revision)) (qmax (emit-ident dialect "_sel_revision"))
                       (qfirst (emit-ident dialect "_sel_first"))
                       (partition-sql (as-value
                                       (emit-text-operand dialect (%fragment (list (emit-ident dialect partition)) (getf pf :type) dialect))))
                       (parts (append (list (format nil "WITH ~a AS (" qi)) (fragment-parts sql)
                                      (list (format nil "), ~a AS (SELECT MAX(~a) AS ~a, MIN(~a) AS ~a FROM ~a GROUP BY ~a) SELECT ~a.* FROM ~a JOIN ~a ON ~a.~a = ~a.~a ORDER BY ~a.~a ASC"
                                                    qg qr qmax qr qfirst qi partition-sql qi qi qg qi qr qg qmax qg qfirst))))
                       (cont-root (sel::make-node :var (sel::node-pos bucket)))
                       (cont nil))
                  (setf (sel::node-s cont-root) "_INPUT"
                        cont (funcall wrap (sel::build-pipeline-ast cont-root (subseq steps at))))
                  (make-hybrid-plan :dialect dialect :sql-statement
                    (%fragment parts :statement dialect (fragment-params sql) (fragment-param-kinds sql) (fragment-caveats sql))
                    :sql-prefix-ast prefix :continuation-ast cont :continuation-program (sel::%make-program "" cont)
                    :source-tables (list (getf rel :from))
                    :selected-member (list :partition-key partition :revision-key revision))))))))
    (sql-error () nil)))

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

(defun join-rows-lack-binders-p (steps)
  "Whether the SQL rows for STEPS are a join's rows without the binders SEL's
rows carry. A LINK's row in SEL holds each side under its binders and the
promoted fields beside them (spec §7.4); SQL carries the promoted fields
alone. A MAP, a SELECT_COLS or a projected BUCKET after the LINK makes the
rows exact again -- what they compute is over the promoted fields, or is
refused -- so a prefix whose LINK nothing has projected is not a split point
and not a full pushdown (finding Y, lanes): its continuation would read
`_[\"C\"]` where the database sent nothing."
  (let ((joined nil))
    (dolist (step steps joined)
      (let ((name (sel::node-s step)))
        (cond ((member name '("LINK" "LINK_LEFT") :test #'equal) (setf joined t))
              ((member name '("MAP" "SELECT_COLS" "BUCKET") :test #'equal) (setf joined nil)))))))

(defun rows-are-not-the-value-p (steps)
  "The two together: a prefix whose SQL rows are not the value SEL would have
produced for it, whatever the translator says about it."
  (or (bucket-rows-are-keys-p steps) (join-rows-lack-binders-p steps)))

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

(defun contains-unsupported-sql-p (node dialect &optional defs seen)
  "Returns T if NODE contains any function call not supported by DIALECT.
DEFS are the helper definitions (an alist of name to node): a read of one is as
unsupported as its definition, since the translator will inline it. SEEN is the
list of helper names already looked through on this path."
  (labels ((walk (n seen)
             (when (and n (sel::node-p n))
               (when (eq (sel::node-kind n) :var)
                 (let ((cell (and defs (assoc (sel::node-s n) defs :test #'equal))))
                   (when (and cell (not (member (sel::node-s n) seen :test #'equal)))
                     (return-from walk (walk (cdr cell) (cons (sel::node-s n) seen))))))
               (when (eq (sel::node-kind n) :call)
                 (let ((name (sel::node-s n)))
                   (unless (member name +sql-special-calls+ :test #'equal)
                     (multiple-value-bind (entry found) (dialect-entry dialect :funcs name)
                       (when (or (not found) (null entry) (stringp entry))
                         (return-from walk t))))))
               ;; Every child, whatever the kind: a list literal holds calls too.
               (let ((unsupported nil))
                 (walk-node-children n (lambda (c) (unless unsupported
                                                     (when (walk c seen) (setf unsupported t)))))
                 unsupported))))
    (walk node seen)))

;; The steps the MAP fall-through may push past the MAP. Each keeps the rows as
;; they are -- the same records, fewer or reordered -- so the custom half of the
;; projection still runs over its own input. A step that changes the row shape
;; (MAP, SELECT_COLS, LINK, BUCKET) would put it over something else, and the
;; whole-row comparisons (DEDUPE, DISTINCT, the keyless sorts) would compare the
;; dependency columns SQL carries where SEL compares the custom values.
;; FILTER retains ordinal keys that SQL rows plus the local MAP cannot restore.
(defparameter +fallthrough-downstream+ '("SORT_BY" "TOP_BY" "TAKE" "DROP"))

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

(defun try-plan-fallthrough (source-node steps dialect bindings options input-var defs wrap)
  "Detects an early MAP whose RECORD mixes translatable pairs with custom ones.
Rewrites the SQL prefix to project the translatable pairs plus the columns the
custom pairs depend on, pushes the remaining pipeline to SQL, and evaluates the
custom pairs on the rows that come back. DEFS are the helper definitions a pair
may read; WRAP puts a tree behind the assignments it depends on."
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
                   (equal (sel::node-s rec-node) "RECORD")
                   (evenp (length (sel::node-items rec-node)))
                   (loop for (k nil) on (sel::node-items rec-node) by #'cddr
                         always (and (sel::node-p k) (eq (sel::node-kind k) :text))))
        (return-from try-plan-fallthrough nil))
      ;; Each pair is (key-node value-node pushable-p).
      ;; Splitting a duplicate RECORD between SQL and local fields changes
      ;; last-write order and may suppress evaluation of overwritten fields.
      (let ((seen (make-hash-table :test #'equal)))
        (loop for (k nil) on (sel::node-items rec-node) by #'cddr do
          (let ((key (sel::node-s k)))
            (when (gethash key seen) (return-from try-plan-fallthrough nil))
            (setf (gethash key seen) t))))
      (let* ((pairs (loop for (k v) on (sel::node-items rec-node) by #'cddr
                          collect (list k v (not (contains-unsupported-sql-p v dialect defs)))))
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
                       (rewritten-ast (funcall wrap (sel::build-pipeline-ast source-node rewritten-steps)))
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
                      (let ((cont-ast (funcall wrap cont-map)))
                        (make-hybrid-plan
                         :sql-statement sql-frag
                         :sql-prefix-ast rewritten-ast
                         :continuation-ast cont-ast
                         :continuation-program (sel::%make-program "" cont-ast)
                         :continuation-source-var input-var
                         :pure-sql-p nil
                         :pure-memory-p nil)))))))))))))

(defun execute-hybrid (plan db-runner &optional context)
  "Execute a HYBRID-PLAN using DB-RUNNER for SQL execution and SEL:RUN for in-memory continuation.
DB-RUNNER is a function (lambda (sql-string params) ...) that returns a SEL:VALUE
(e.g. list of rows). It is handed the statement in :PARAMS mode -- text
literals as `?` placeholders, numbers inlined -- and BINDINGS, the bound values
in placeholder order, which is what the other four hosts hand their runners
(review finding AK: this host handed inline SQL and the creation-order slot
list, which a driver could not bind as it was)."
  (cond
    ((hybrid-plan-pure-sql-p plan)
     (let ((frag (hybrid-plan-sql-statement plan)))
       (funcall db-runner (as-statement frag :params) (bindings frag))))
    ((hybrid-plan-pure-memory-p plan)
     (sel:run (hybrid-plan-continuation-program plan) context))
    (t
     ;; Hybrid execution: DB first, then in-memory continuation
     (let* ((frag (hybrid-plan-sql-statement plan))
            (db-rows (funcall db-runner (as-statement frag :params) (bindings frag)))
            (cont-prog (hybrid-plan-continuation-program plan))
            (input-var (hybrid-plan-continuation-source-var plan)))
       (let ((cont-context (sel:make-none)))
         (when context
           (let ((c-val (if (sel:value-p context) context (sel:from-native context))))
             (dolist (k (sel:value-keys c-val))
               (sel:value-set cont-context k (sel:value-get c-val k)))))
         (sel:value-set cont-context input-var (if (sel:value-p db-rows) db-rows (sel:from-native db-rows)))
         (sel:run cont-prog cont-context))))))
