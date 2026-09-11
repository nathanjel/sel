;;;; hybrid.lisp - Hybrid execution planner for relational pipelines.
;;;; Slices a pipeline into a maximal SQL pushdown prefix and an in-memory continuation.

(in-package #:sel.sql)

(defstruct (hybrid-plan (:constructor make-hybrid-plan))
  (sql-statement nil)
  (sql-prefix-ast nil)
  (continuation-program nil)
  (continuation-source-var "_INPUT" :type string)
  (pure-sql-p nil :type boolean)
  (pure-memory-p nil :type boolean))

(defun build-pipeline-ast (root steps)
  "Rebuilds a pipeline AST with ROOT as the initial source and chaining STEPS."
  (let ((curr root))
    (dolist (step steps curr)
      (let ((new-node (sel::copy-node step)))
        (setf (sel::node-items new-node) (cons curr (rest (sel::node-items step))))
        (setf curr new-node)))))

(defun plan-hybrid (program dialect &optional bindings options)
  "Analyzes PROGRAM and splits it into a maximal SQL pushdown prefix and an in-memory continuation.
Returns a HYBRID-PLAN struct."
  (require-target dialect)
  (let* ((b (make-bindings (or bindings '())))
         (tr (%translator dialect b (and (getf options :strict) t))))
    (bindings-check-aliases (translator-bindings tr))
    (multiple-value-bind (names root) (const-scope (translator-bindings tr))
      (setf (translator-const-names tr) names
            (translator-const-root tr) root)
      (let* ((norm (sel:optimize-ast-logical (normalise (sel:program-ast program) names root)))
             (steps '())
             (curr norm))
        (loop while (and curr
                         (not (clist-p curr))
                         (eq (snode-kind curr) :call)
                         (member (sel::node-s curr) +pipeline-ops+ :test #'equal)
                         (sel::node-items curr))
              do (push curr steps)
                 (setf curr (first (sel::node-items curr))))
        ;; If no steps or source is not a bound relation, cannot push down anything
        (unless (and steps curr (not (clist-p curr)) (eq (snode-kind curr) :var))
          (return-from plan-hybrid
            (make-hybrid-plan :pure-memory-p t :continuation-program program)))
        (let ((name (sel::node-s curr)))
          (unless (bindings-has (translator-bindings tr) name)
            (return-from plan-hybrid
              (make-hybrid-plan :pure-memory-p t :continuation-program program)))
          (let ((rel-b (bindings-get (translator-bindings tr) name (snode-pos curr))))
            (unless (eq (binding-kind rel-b) :relation)
              (return-from plan-hybrid
                (make-hybrid-plan :pure-memory-p t :continuation-program program)))))

(defun collect-field-references (node &optional (binder "_"))
  "Collects all field names accessed via BINDER['field'] in NODE."
  (let ((refs '()))
    (labels ((walk (n)
               (when (and n (sel::node-p n))
                 (if (and (eq (sel::node-kind n) :index)
                          (let ((l (sel::node-l n))
                                (r (sel::node-r n)))
                            (and l (not (clist-p l)) (eq (sel::node-kind l) :var)
                                 (or (string-equal (sel::node-s l) binder)
                                     (string-equal (sel::node-s l) "_")
                                     (string-equal (sel::node-s l) "_1")
                                     (string-equal (sel::node-s l) "_2"))
                                 r (not (clist-p r)) (eq (sel::node-kind r) :text))))
                     (pushnew (sel::node-s (sel::node-r n)) refs :test #'string-equal)
                     (case (sel::node-kind n)
                       (:index (walk (sel::node-l n)) (walk (sel::node-r n)))
                       (:call (dolist (item (sel::node-items n)) (walk item)))
                       (:bin (walk (sel::node-l n)) (walk (sel::node-r n)))
                       (:un (walk (sel::node-l n)))
                       (:group (walk (sel::node-l n))))))))
      (walk node))
    refs))

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
                 (case (sel::node-kind n)
                   (:call
                    (let ((name (sel::node-s n)))
                      (unless (member name +sql-special-calls+ :test #'equal)
                        (multiple-value-bind (entry found) (dialect-entry dialect :funcs name)
                          (when (or (not found) (null entry) (stringp entry))
                            (setf unsupported t))))
                      (dolist (item (sel::node-items n)) (walk item))))
                   (:bin (walk (sel::node-l n)) (walk (sel::node-r n)))
                   (:un (walk (sel::node-l n)))
                   (:index (walk (sel::node-l n)) (walk (sel::node-r n)))
                   (:group (walk (sel::node-l n)))))))
      (walk node))
    unsupported))

(defun try-plan-fallthrough (source-node steps dialect bindings options input-var)
  "Detects if an early MAP contains custom/unsupported functions whose output
fields are NOT referenced in downstream operations. Rewrites the SQL prefix to
pass through the dependency columns, pushes down the remaining pipeline to SQL,
and evaluates the custom function on the final DB rows in memory."
  (let ((map-idx (position "MAP" steps :key (lambda (s) (sel::node-s s)) :test #'equal)))
    (unless map-idx (return-from try-plan-fallthrough nil))
    (let* ((map-step (nth map-idx steps))
           (args (sel::node-items map-step)))
      (multiple-value-bind (binder rec-node)
          (if (= (length args) 2)
              (values "_" (second args))
              (if (= (length args) 3)
                  (values (sel::node-s (second args)) (third args))
                  (return-from try-plan-fallthrough nil)))
        (unless (and rec-node (not (clist-p rec-node))
                     (eq (sel::node-kind rec-node) :call)
                     (equal (sel::node-s rec-node) "RECORD"))
          (return-from try-plan-fallthrough nil))
        (let* ((items (sel::node-items rec-node)))
          (unless (evenp (length items))
            (return-from try-plan-fallthrough nil))
          (let ((pushable-pairs '())
                (custom-pairs '()))
            (loop for (k-node v-node) on items by #'cddr do
              (let ((k-name (sel::node-s k-node)))
                (if (contains-unsupported-sql-p v-node dialect)
                    (push (cons k-name (list k-node v-node)) custom-pairs)
                    (push (cons k-name (list k-node v-node)) pushable-pairs))))
            (setf pushable-pairs (nreverse pushable-pairs)
                  custom-pairs (nreverse custom-pairs))
            ;; If all are pushable or none are pushable, nothing to fall through here
            (when (or (null custom-pairs) (null pushable-pairs))
              (return-from try-plan-fallthrough nil))


            ;; Check if any custom key is referenced in downstream steps
            (let* ((downstream-steps (subseq steps (1+ map-idx)))
                   (downstream-refs (collect-all-step-field-references downstream-steps)))
              (when (some (lambda (cp) (member (car cp) downstream-refs :test #'string-equal))
                          custom-pairs)
                (return-from try-plan-fallthrough nil))

              ;; Collect dependencies needed by custom expressions
              (let ((all-deps '()))
                (dolist (cp custom-pairs)
                  (let ((deps (collect-field-references (second (cdr cp)) binder)))
                    (dolist (d deps)
                      (pushnew d all-deps :test #'string-equal))))

                ;; Build rewritten RECORD node for SQL
                (let ((new-items '()))
                  ;; Keep pushable pairs
                  (dolist (pp pushable-pairs)
                    (push (first (cdr pp)) new-items)
                    (push (second (cdr pp)) new-items))
                  ;; Add dependency columns if not already projected
                  (dolist (d all-deps)
                    (unless (assoc d pushable-pairs :test #'string-equal)
                      (let ((k-n (let ((n (sel::copy-node rec-node)))
                                   (setf (sel::node-kind n) :text
                                         (sel::node-s n) d
                                         (sel::node-items n) nil)
                                   n))
                            (v-n (let ((idx-n (sel::copy-node rec-node))
                                       (var-n (sel::copy-node rec-node))
                                       (txt-n (sel::copy-node rec-node)))
                                   (setf (sel::node-kind var-n) :var
                                         (sel::node-s var-n) binder
                                         (sel::node-items var-n) nil)
                                   (setf (sel::node-kind txt-n) :text
                                         (sel::node-s txt-n) d
                                         (sel::node-items txt-n) nil)
                                   (setf (sel::node-kind idx-n) :index
                                         (sel::node-l idx-n) var-n
                                         (sel::node-r idx-n) txt-n
                                         (sel::node-items idx-n) nil)
                                   idx-n)))
                        (push k-n new-items)
                        (push v-n new-items))))
                  (setf new-items (nreverse new-items))

                  (let* ((rewritten-rec (sel::copy-node rec-node))
                         (rewritten-map (sel::copy-node map-step)))
                    (setf (sel::node-items rewritten-rec) new-items)
                    (if (= (length args) 2)
                        (setf (sel::node-items rewritten-map)
                              (list (first args) rewritten-rec))
                        (setf (sel::node-items rewritten-map)
                              (list (first args) (second args) rewritten-rec)))

                    ;; Construct the rewritten pipeline steps
                    (let* ((rewritten-steps (append (subseq steps 0 map-idx)
                                                    (list rewritten-map)
                                                    downstream-steps))
                           (rewritten-ast (build-pipeline-ast source-node rewritten-steps))
                           (rewritten-prog (sel::%make-program "" rewritten-ast))
                           (sql-frag (try-translate-statement rewritten-prog dialect bindings options)))
                      (when sql-frag
                        ;; Build continuation program on _INPUT
                        (let* ((cont-root (let ((v (sel::make-node :var (sel::node-pos map-step))))
                                            (setf (sel::node-s v) input-var)
                                            v))
                               (cont-map (sel::copy-node map-step)))
                          (if (= (length args) 2)
                              (setf (sel::node-items cont-map) (list cont-root rec-node))
                              (setf (sel::node-items cont-map) (list cont-root (second args) rec-node)))
                          (let ((cont-prog (sel::%make-program "" cont-map)))
                            (make-hybrid-plan
                             :sql-statement sql-frag
                             :sql-prefix-ast rewritten-ast
                             :continuation-program cont-prog
                             :continuation-source-var input-var
                             :pure-sql-p nil
                             :pure-memory-p nil)))))))))))))))

        ;; Try prefixes of decreasing length from N down to 1
        (let ((n-steps (length steps))
              (source-node curr)
              (input-var "_INPUT"))

          ;; 1. Check for full pushdown
          (let* ((full-ast (build-pipeline-ast source-node steps))
                 (full-prog (sel::%make-program "" full-ast))
                 (full-frag (try-translate-statement full-prog dialect bindings options)))
            (when full-frag
              (return-from plan-hybrid
                (make-hybrid-plan
                 :sql-statement full-frag
                 :sql-prefix-ast full-ast
                 :pure-sql-p t))))

          ;; 2. Check for pass-through fallthrough optimization
          (let ((ft-plan (try-plan-fallthrough source-node steps dialect bindings options input-var)))
            (when ft-plan
              (return-from plan-hybrid ft-plan)))

          ;; 3. Check for prefix pushdown (mid-pipeline memory continuation)
          (loop for k from (1- n-steps) downto 1 do
            (let* ((prefix-steps (subseq steps 0 k))
                   (prefix-ast (build-pipeline-ast source-node prefix-steps))
                   (prefix-prog (sel::%make-program "" prefix-ast))
                   (frag (try-translate-statement prefix-prog dialect bindings options)))
              (when frag
                (let* ((rem-steps (subseq steps k))
                       (cont-root (let ((v (sel::make-node :var (sel::node-pos (first rem-steps)))))
                                    (setf (sel::node-s v) input-var)
                                    v))
                       (cont-ast (build-pipeline-ast cont-root rem-steps))
                       (cont-prog (sel::%make-program "" cont-ast)))
                  (return-from plan-hybrid
                    (make-hybrid-plan
                     :sql-statement frag
                     :sql-prefix-ast prefix-ast
                     :continuation-program cont-prog
                     :continuation-source-var input-var
                     :pure-sql-p nil
                     :pure-memory-p nil))))))

          ;; If no prefix succeeded, pure in-memory execution
          (make-hybrid-plan :pure-memory-p t :continuation-program program))))))

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
