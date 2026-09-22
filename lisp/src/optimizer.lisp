;;;; optimizer.lisp - AST Pipeline Optimizer for in-memory and hybrid execution.
;;;;
;;;; Applies:
;;;; 1. Top-N Fusion: Fuses SORT / SORT_DESC / SORT_BY + TAKE into TOP / TOP_DESC / TOP_BY
;;;; 2. Filter Pushdown: Pushes FILTER before MAP when filter predicate depends only on pass-through columns
;;;; 3. Top-N Pushdown (Late Materialization): Pushes TOP_BY / SORT_BY before MAP when sort key depends only on pass-through columns

(in-package #:sel)

(defparameter +pipeline-ops+
  '("FILTER" "BUCKET" "SELECT_COLS" "MAP" "DISTINCT" "DEDUPE" "TAKE" "DROP"
    "SORT" "SORT_DESC" "SORT_BY" "TOP" "TOP_DESC" "TOP_BY" "LINK" "LINK_LEFT"))

(defun copy-node-shallow (n)
  (let ((copy (make-node (node-kind n) (node-pos n))))
    (setf (node-s copy) (node-s n)
          (node-b copy) (node-b n)
          (node-grouped copy) (node-grouped n)
          (node-l copy) (node-l n)
          (node-r copy) (node-r n)
          (node-items copy) (copy-list (node-items n))
          (node-spec copy) (node-spec n)
          (node-record-shape copy) (node-record-shape n)
          (node-math-plan copy) (node-math-plan n)
          (node-tentative copy) (node-tentative n)
          (node-pushed-down copy) (node-pushed-down n)
          (node-remaining copy) (node-remaining n))
    copy))

;; A fold that replaces a node by one of its children must not move the error
;; position an operator over the result reports: spec §6.3 names the node that
;; actually failed, and to the operator the operand IS the folded node, not the
;; literal inside it (ctl.if.constant-condition-result-keeps-the-if-position).
;; So a hoisted child is re-stamped with the folded node's position -- which is
;; only exact for a leaf literal, the one shape that carries no positions of
;; its own and cannot fail by itself. A variable is a leaf that can (E_UNDEF_VAR
;; at its own column), so it is not a literal here.
(defun literal-node-p (node)
  (and node (member (node-kind node) '(:num :text :bool :null))))

(defun hoist-literal (child pos)
  (let ((copy (copy-node-shallow child)))
    (setf (node-pos copy) pos)
    copy))

(defun literal-bool (value pos)
  (let ((res (make-node :bool pos)))
    (setf (node-b res) value)
    res))

(defun fold-node (node)
  "Folds scalar constants and eliminates dead branches according to strict SEL semantics."
  (when node
    (case (node-kind node)
      (:un
       (let ((child (node-l node))
             (op (node-s node)))
         (cond
           ((and (string= op "NOT") child (eq (node-kind child) :bool))
            (let ((res (make-node :bool (node-pos node))))
              (setf (node-b res) (not (node-b child)))
              res))
           ;; Through the decimal core, as the other hosts and the evaluator:
           ;; -0 is 0, not "-0". A numeral the core refuses is left for the
           ;; evaluator, which owns that error.
           ((and (string= op "NEG") child (eq (node-kind child) :num))
            (handler-case
                (let ((d (dec-parse (node-s child) (node-pos node))))
                  (if d
                      (let ((res (make-node :num (node-pos node))))
                        (setf (node-s res) (dec-format (dec-negate d)))
                        res)
                      node))
              (error () node)))
           (t node))))

      (:bin
       (let ((l (node-l node))
             (r (node-r node))
             (op (node-s node)))
         (cond
           ;; Short-circuit and literal boolean logic
           ((string= op "AND")
            (cond
              ;; FALSE AND x -> FALSE (left operand is literally FALSE, short-circuits without touching x)
              ((and l (eq (node-kind l) :bool) (null (node-b l))) (literal-bool nil (node-pos node)))
              ;; Literal bool AND bool
              ((and l r (eq (node-kind l) :bool) (eq (node-kind r) :bool))
               (let ((res (make-node :bool (node-pos node))))
                 (setf (node-b res) (and (node-b l) (node-b r)))
                 res))
              (t node)))

           ((string= op "OR")
            (cond
              ;; TRUE OR x -> TRUE (left operand is literally TRUE, short-circuits without touching x)
              ((and l (eq (node-kind l) :bool) (node-b l)) (literal-bool t (node-pos node)))
              ;; Literal bool OR bool
              ((and l r (eq (node-kind l) :bool) (eq (node-kind r) :bool))
               (let ((res (make-node :bool (node-pos node))))
                 (setf (node-b res) (or (node-b l) (node-b r)))
                 res))
              (t node)))

           ;; Numeric arithmetic
           ((and l r (eq (node-kind l) :num) (eq (node-kind r) :num)
                 (member op '("+" "-" "*" "/" "%") :test #'string=))
            (let ((pos (node-pos node)))
              (handler-case
                  (let* ((dl (dec-parse (node-s l) pos))
                         (dr (dec-parse (node-s r) pos))
                         (dres (when (and dl dr)
                                 (cond
                                   ((string= op "+") (dec-add dl dr pos))
                                   ((string= op "-") (dec-sub dl dr pos))
                                   ((string= op "*") (dec-mul dl dr pos))
                                   ((string= op "/") (dec-div dl dr pos))
                                   ((string= op "%") (dec-mod dl dr pos))
                                   (t nil)))))
                    (if dres
                        (let ((res (make-node :num pos)))
                          (setf (node-s res) (dec-format dres))
                          res)
                        node))
                (error () node))))

           ;; Numeric comparisons
           ((and l r (eq (node-kind l) :num) (eq (node-kind r) :num)
                 (member op '("==" "!=" "<" "<=" ">" ">=") :test #'string=))
            (let ((pos (node-pos node)))
              (handler-case
                  (let* ((dl (dec-parse (node-s l) pos))
                         (dr (dec-parse (node-s r) pos)))
                    (if (and dl dr)
                        (let* ((cmp (dec-cmp dl dr))
                               (b (cond
                                    ((string= op "==") (zerop cmp))
                                    ((string= op "!=") (not (zerop cmp)))
                                    ((string= op "<")  (< cmp 0))
                                    ((string= op "<=") (<= cmp 0))
                                    ((string= op ">")  (> cmp 0))
                                    ((string= op ">=") (>= cmp 0)))))
                          (let ((res (make-node :bool pos)))
                            (setf (node-b res) b)
                            res))
                        node))
                (error () node))))

           ;; String comparisons
           ((and l r (eq (node-kind l) :text) (eq (node-kind r) :text)
                 (member op '("$==" "$!=" "$<" "$<=" "$>" "$>=") :test #'string=))
            (let* ((sl (node-s l))
                   (sr (node-s r))
                   (b (cond
                        ((string= op "$==") (string= sl sr))
                        ((string= op "$!=") (string/= sl sr))
                        ((string= op "$<")  (string< sl sr))
                        ((string= op "$<=") (string<= sl sr))
                        ((string= op "$>")  (string> sl sr))
                        ((string= op "$>=") (string>= sl sr)))))
              (let ((res (make-node :bool (node-pos node))))
                (setf (node-b res) b)
                res)))

           (t node))))

      (:call
       (let ((name (node-s node))
             (items (node-items node)))
         (if (and (string= name "IF") (= (length items) 3))
             (let ((cond-node (first items)))
               (if (and cond-node (eq (node-kind cond-node) :bool))
                   (let ((branch (if (node-b cond-node) (second items) (third items))))
                     (if (literal-node-p branch)
                         (hoist-literal branch (node-pos node))
                         node))
                   node))
             node)))

      (t node))))

(defun build-pipeline-ast (root steps)
  (let ((curr root))
    (dolist (step steps curr)
      (let ((new-node (copy-node-shallow step)))
        (setf (node-items new-node) (cons curr (rest (node-items step))))
        (setf curr new-node)))))

(defun unwind-pipeline (node)
  (let ((steps '())
        (curr node))
    (loop while (and curr
                     (node-p curr)
                     (eq (node-kind curr) :call)
                     (member (node-s curr) +pipeline-ops+ :test #'string=)
                     (node-items curr))
          do (push curr steps)
             (setf curr (first (node-items curr))))
    (values curr steps)))

(defun collect-field-refs (node &optional (binder "_"))
  (let ((refs '()))
    (labels ((walk (n)
               (when (and n (node-p n))
                 (if (and (eq (node-kind n) :index)
                          (let ((l (node-l n))
                                (r (node-r n)))
                            (and l (eq (node-kind l) :var)
                                 (or (string= (node-s l) binder)
                                     (string= (node-s l) "_")
                                     (string= (node-s l) "_1")
                                     (string= (node-s l) "_2"))
                                 r (eq (node-kind r) :text))))
                     (pushnew (node-s (node-r n)) refs :test #'string=)
                     (case (node-kind n)
                       (:index (walk (node-l n)) (walk (node-r n)))
                       (:call (dolist (item (node-items n)) (walk item)))
                       (:bin (walk (node-l n)) (walk (node-r n)))
                       (:un (walk (node-l n)))
                       (:assign (walk (node-l n)) (walk (node-r n))))))))
      (walk node))
    refs))

(defun map-passthrough-fields (map-step)
  "Returns a list of field names that MAP passes through unchanged from input."
  (let* ((args (node-items map-step))
         (count (length args))
         (binder (if (= count 3) (node-s (second args)) "_"))
         (body (if (= count 3) (third args) (second args))))
    (when (and (eq (node-kind body) :call)
               (string= (node-s body) "RECORD"))
      (let ((items (node-items body))
            (passthroughs '()))
        (loop for (k-node v-node) on items by #'cddr
              when (and (eq (node-kind k-node) :text)
                        (eq (node-kind v-node) :index)
                        (let ((l (node-l v-node))
                              (r (node-r v-node)))
                          (and l (eq (node-kind l) :var)
                               (string= (node-s l) binder)
                               r (eq (node-kind r) :text)
                               (string= (node-s r) (node-s k-node)))))
              do (push (node-s k-node) passthroughs))
        passthroughs))))

(defun map-has-computed-fields-p (map-step)
  "Returns T if MAP computes any field non-trivially (not just simple pass-through)."
  (let* ((args (node-items map-step))
         (count (length args))
         (binder (if (= count 3) (node-s (second args)) "_"))
         (body (if (= count 3) (third args) (second args))))
    (if (and (eq (node-kind body) :call)
             (string= (node-s body) "RECORD"))
        (let ((items (node-items body)))
          (loop for (k-node v-node) on items by #'cddr
                thereis (not (and (eq (node-kind k-node) :text)
                                  (eq (node-kind v-node) :index)
                                  (let ((l (node-l v-node))
                                        (r (node-r v-node)))
                                    (and l (eq (node-kind l) :var)
                                         (string= (node-s l) binder)
                                         r (eq (node-kind r) :text)
                                         (string= (node-s r) (node-s k-node))))))))
        t)))

(defun reads-var-p (node names)
  "Whether NODE reads one of NAMES as a variable -- other than as
name[\"field\"], which is a field read. Identifiers are upcased by the lexer."
  (labels ((walk (n)
             (when (and n (node-p n))
               (case (node-kind n)
                 (:var (member (node-s n) names :test #'string=))
                 (:index
                  (let ((l (node-l n)) (r (node-r n)))
                    (unless (and l (eq (node-kind l) :var) r (eq (node-kind r) :text))
                      (or (walk l) (walk r)))))
                 ((:call :list :seq) (some #'walk (node-items n)))
                 ((:bin :assign) (or (walk (node-l n)) (walk (node-r n))))
                 (:un (walk (node-l n)))
                 (t nil)))))
    (and (walk node) t)))

(defun reads-row-or-key-p (node &optional (binder "_"))
  "Whether a body reads the element as a whole (its binder, or any of the
pipeline's implicit names) or its key _K. The field set says what a rewrite
may rely on; this says when it may not: a body that reads either cannot move
across a step that changes the rows' shape (MAP, SELECT_COLS) or renumbers
them (MAP, SELECT_COLS, the sorts)."
  (reads-var-p node (list binder "_" "_1" "_2" "_K")))

(defun step-reads-key-p (step)
  "Whether a step's own arguments (not its input) read _K: the keys a sort
renumbers, so such a step keeps its place relative to one."
  (some (lambda (arg) (reads-var-p arg '("_K"))) (rest (node-items step))))

(defun keys-renumbered-by-p (step)
  "Whether the step after a FILTER hides where the FILTER ran. FILTER keeps its
input's keys (spec §7.3) and MAP, SELECT_COLS and the sorts renumber, so a
FILTER moved in front of one of them carries the source's keys where the
program as written carried the step's -- visible in the answer, and in any
later `_K`. Only a following step that renumbers again without reading `_K`
hides that; the end of the pipeline, or another FILTER, does not."
  (and step
       (string/= (node-s step) "FILTER")
       (not (step-reads-key-p step))))

(defun source-is-list-p (source)
  "Whether the source a pipeline starts from is a list already, so a FILTER
whose predicate is a constant TRUE over it is the identity. Over a scalar it
is not: FILTER wraps a scalar into a one-element list (spec §7.3), and only a
later step, a list literal or a constructor is known not to be one."
  (and source (node-p source)
       (or (eq (node-kind source) :list)
           (and (eq (node-kind source) :call)
                (member (node-s source) '("LIST" "RECORD") :test #'string=)))))

(defvar *fold-constants* t
  "The other hosts' foldConstants option: off for the one slot whose shape
the evaluator reads (STEP-ARG-FOLDS-P).")

(defun step-arg-folds-p (step index)
  "The evaluator resolves the three-argument SORT_BY / TOP_BY form by shape
(spec §7.3): a text literal in the third slot is the direction, otherwise a
bare name in the second slot is the binder and the third slot is its key. A
fold that hoists a text literal into that slot -- IF(TRUE, \"DESC\", \"ASC\")
-- would change the form, so the slot is walked without folding."
  (let* ((args (node-items step))
         (sort-count (cond ((string= (node-s step) "SORT_BY") (length args))
                           ((string= (node-s step) "TOP_BY") (1- (length args)))
                           (t 0))))
    (not (and (= sort-count 3) (= index 2)
              (eq (node-kind (second args)) :var)
              (not (node-grouped (second args)))))))

(defun filter-body (filter-step)
  "The binder and predicate of a FILTER step, as two values."
  (let* ((args (node-items filter-step))
         (count (length args)))
    (values (if (= count 3) (node-s (second args)) "_")
            (if (= count 3) (third args) (second args)))))

(defun filter-fields (filter-step)
  (multiple-value-bind (binder pred) (filter-body filter-step)
    (collect-field-refs pred binder)))

(defun bare-name-p (node)
  (and node (node-p node) (eq (node-kind node) :var) (not (node-grouped node))))

(defun sort-key (sort-step)
  "The binder and key of a sort step, as two values; a keyless sort has no
key. The forms are the evaluator's (spec §7.3)."
  (let* ((args (node-items sort-step))
         (count (length args))
         (sname (node-s sort-step))
         (binder "_")
         (key nil))
    (cond
      ((member sname '("SORT" "SORT_DESC") :test #'string=)
       (unless (= count 1)
         (setf binder (if (and (= count 3) (bare-name-p (second args))) (node-s (second args)) "_")
               key (if (= count 3) (third args) (second args)))))
      ((member sname '("TOP" "TOP_DESC") :test #'string=)
       ;; TOP(source, key, n) has three arguments and TOP(source, binder, key, n)
       ;; has four; the count below excludes n.
       (unless (= count 2)
         (let ((sort-count (1- count)))
           (setf binder (if (and (= sort-count 3) (bare-name-p (second args))) (node-s (second args)) "_")
                 key (if (= sort-count 3) (third args) (second args))))))
      ((member sname '("SORT_BY" "TOP_BY") :test #'string=)
       (let ((sort-count (if (string= sname "TOP_BY") (1- count) count)))
         (cond
           ((or (= sort-count 2)
                (and (= sort-count 3) (eq (node-kind (third args)) :text)))
            (setf key (second args)))
           ((and (= sort-count 3) (bare-name-p (second args)))
            (setf binder (node-s (second args)) key (third args)))
           ((and (> count 2) (bare-name-p (second args)))
            (setf binder (node-s (second args)) key (third args)))))))
    (values binder key)))

(defun sort-fields (sort-step)
  (multiple-value-bind (binder key) (sort-key sort-step)
    (and key (collect-field-refs key binder))))

(defun select-cols-fields (step)
  (let* ((col-args (rest (node-items step)))
         (items (if (and (= (length col-args) 1) (eq (node-kind (first col-args)) :list))
                    (node-items (first col-args))
                    col-args)))
    (loop for it in items
          when (eq (node-kind it) :text)
          collect (node-s it))))

(defun try-parse-int-literal (node)
  "A non-negative integer literal's value, or NIL: what the slicing rules may
fold, as in the other hosts (a negative count is the evaluator's error)."
  (when (and node (node-p node) (eq (node-kind node) :num))
    (let ((s (node-s node)))
      (when (and (plusp (length s))
                 (every (lambda (c) (char<= #\0 c #\9)) s))
        (ignore-errors (parse-integer s))))))

(defun split-and-conjuncts (node)
  (if (and node (node-p node) (eq (node-kind node) :bin) (string= (node-s node) "AND"))
      (append (split-and-conjuncts (node-l node))
              (split-and-conjuncts (node-r node)))
      (list node)))

(defun combine-and-conjuncts (nodes &optional pos)
  (cond
    ((null nodes) nil)
    ((= (length nodes) 1) (first nodes))
    (t
     (let ((res (first nodes)))
       (dolist (nxt (rest nodes))
         (let ((bin (make-node :bin (or pos (node-pos res)))))
           (setf (node-s bin) "AND"
                 (node-l bin) res
                 (node-r bin) nxt)
           (setf res bin)))
       res))))

(defun rename-var-in-node (node old-var new-var)
  (when (and node (node-p node))
    (let ((c (copy-node-shallow node)))
      (when (and (eq (node-kind c) :var) (string= (node-s c) old-var))
        (setf (node-s c) new-var))
      (when (node-l c) (setf (node-l c) (rename-var-in-node (node-l c) old-var new-var)))
      (when (node-r c) (setf (node-r c) (rename-var-in-node (node-r c) old-var new-var)))
      (when (node-items c)
        (setf (node-items c) (mapcar (lambda (it) (rename-var-in-node it old-var new-var)) (node-items c))))
      c)))

(defun collect-pipeline-source-names (node)
  "Collects table/relation names mentioned in NODE (left sources of joins/pipes)."
  (let ((names '()))
    (labels ((walk (n)
               (when (and n (node-p n))
                 (cond
                   ((eq (node-kind n) :var)
                    (pushnew (node-s n) names :test #'string-equal))
                   ((eq (node-kind n) :call)
                    (let ((op (node-s n))
                          (items (node-items n)))
                      (cond
                        ((member op '("LINK" "LINK_LEFT") :test #'string=)
                         (walk (first items))
                         (walk (second items)))
                        ((member op +pipeline-ops+ :test #'string=)
                         (walk (first items)))
                        (t nil))))))))
      (walk node))
    names))

(defun conjunct-relation-affinity (c left-names right-names binder)
  "Classifies CONJUNCT C as :LEFT, :RIGHT, or :UNKNOWN."
  (let ((has-left nil)
        (has-right nil)
        (has-ambiguous nil)
        (has-unknown nil))
    (labels ((walk (n)
               (when (and n (node-p n))
                 (cond
                   ;; Pattern 1: _['tbl']['col']
                   ((and (eq (node-kind n) :index)
                         (let ((l (node-l n)))
                           (and l (eq (node-kind l) :index)
                                (let ((ll (node-l l))
                                      (lr (node-r l)))
                                  (and ll (eq (node-kind ll) :var)
                                       (or (string= (node-s ll) binder)
                                           (string= (node-s ll) "_"))
                                       lr (eq (node-kind lr) :text))))))
                    (let* ((l (node-l n))
                           (tbl (node-s (node-r l))))
                      (cond
                        ((member tbl left-names :test #'string-equal)
                         (setf has-left t))
                        ((member tbl right-names :test #'string-equal)
                         (setf has-right t))
                        (t
                         (setf has-unknown t)))))

                   ;; Pattern 2: NAME['col']. `O["id"]` or `ORDERS["id"]` after
                   ;; the LINK: the binders are scoped to the predicate (spec
                   ;; §7.4), so as written this is E_UNDEF_VAR, or E_NO_KEY on
                   ;; the relation's list. Pushing it into the side it names
                   ;; turned that error into rows (review 2026-09-15, W2) --
                   ;; only `_["O"]["id"]`, a read through the joined row's key,
                   ;; names a side.
                   ((and (eq (node-kind n) :index)
                         (let ((l (node-l n))
                               (r (node-r n)))
                           (and l (eq (node-kind l) :var) r (eq (node-kind r) :text))))
                    (let ((var-name (node-s (node-l n))))
                      (cond
                        ((or (string= var-name binder) (string= var-name "_"))
                         (setf has-ambiguous t))
                        (t
                         (setf has-unknown t)))))

                   ;; Pattern 3: Bare variable
                   ((eq (node-kind n) :var)
                    (let ((v (node-s n)))
                      (cond
                        ((or (string= v binder) (string= v "_")) nil)
                        (t (setf has-unknown t)))))

                   (t
                    (when (node-l n) (walk (node-l n)))
                    (when (node-r n) (walk (node-r n)))
                    (when (node-items n)
                      (dolist (it (node-items n)) (walk it))))))))
      (walk c))
    (cond
      ((and has-left (not has-right) (not has-ambiguous) (not has-unknown)) :left)
      ((and has-right (not has-left) (not has-ambiguous) (not has-unknown)) :right)
      (t :unknown))))

(defun rewrite-conjunct-for-relation (c target-names binder)
  "Rewrites references in C like _['tbl']['col'] into _['col']."
  (when (and c (node-p c))
    (let ((copy (copy-node-shallow c)))
      (cond
        ;; Pattern 1: _['tbl']['col'] -> _['col']
        ((and (eq (node-kind copy) :index)
              (let ((l (node-l copy)))
                (and l (eq (node-kind l) :index)
                     (let ((ll (node-l l))
                           (lr (node-r l)))
                       (and ll (eq (node-kind ll) :var)
                            (or (string= (node-s ll) binder)
                                (string= (node-s ll) "_"))
                            lr (eq (node-kind lr) :text)
                            (member (node-s lr) target-names :test #'string-equal))))))
         (let ((new-index (make-node :index (node-pos copy)))
               (var-node (make-node :var (node-pos copy))))
           (setf (node-s var-node) "_")
           (setf (node-l new-index) var-node
                 (node-r new-index) (node-r copy))
           new-index))

        ;; No arm for NAME['col']: it is not attributed to a side (see
        ;; CONJUNCT-RELATION-AFFINITY), so it is never rewritten.
        (t
         (when (node-l copy) (setf (node-l copy) (rewrite-conjunct-for-relation (node-l copy) target-names binder)))
         (when (node-r copy) (setf (node-r copy) (rewrite-conjunct-for-relation (node-r copy) target-names binder)))
         (when (node-items copy)
           (setf (node-items copy)
                 (mapcar (lambda (it) (rewrite-conjunct-for-relation it target-names binder)) (node-items copy))))
         copy)))))

(defun sort-step-p (step)
  (member (node-s step) '("SORT" "SORT_DESC" "SORT_BY") :test #'string=))

(defun filter-predicate-of (step)
  "The body of a FILTER step: the third item with an explicit binder, else the second."
  (let ((args (node-items step)))
    (if (= (length args) 3) (third args) (second args))))

(defun valid-filter-p (step)
  "A FILTER whose binder slot, if it has one, is a bare name."
  (let ((args (node-items step)))
    (or (= (length args) 2)
        (and (= (length args) 3) (bare-name-p (second args))))))

(defun fields-all-in-p (fields allowed)
  (every (lambda (f) (member f allowed :test #'string=)) fields))

(defun logical-step-pair (source i s1 s2 s3)
  "The rewrite for the pair (S1 S2) at position I, as two values: the steps
that replace the pair and how many of the two were consumed -- or NIL when no
rule fires. S3 is the step after the pair (NIL at the end of the pipeline),
which only the three FILTER-moving rules look at (KEYS-RENUMBERED-BY-P). The
rules, and their order, are the other four hosts' single left-to-right sweep
(review 2026-09-15 finding V: this host ran them as ten ordered passes, and
`MAP .> SORT_BY .> TAKE` reached the translator in a different shape than
everywhere else)."
  (let ((n1 (node-s s1))
        (n2 (and s2 (node-s s2))))
    (cond
      ;; TAKE + TAKE -> TAKE(min); DROP + DROP -> DROP(sum)
      ((and s2 (string= n1 "TAKE") (string= n2 "TAKE")
            (= (length (node-items s1)) 2) (= (length (node-items s2)) 2)
            (try-parse-int-literal (second (node-items s1)))
            (try-parse-int-literal (second (node-items s2))))
       (let* ((k1 (try-parse-int-literal (second (node-items s1))))
              (k2 (try-parse-int-literal (second (node-items s2))))
              (fused (copy-node-shallow s1))
              (num-node (make-node :num (node-pos (second (node-items s2))))))
         (setf (node-s num-node) (format nil "~d" (min k1 k2))
               (node-items fused) (list (first (node-items s1)) num-node))
         (values (list fused) 2)))
      ((and s2 (string= n1 "DROP") (string= n2 "DROP")
            (= (length (node-items s1)) 2) (= (length (node-items s2)) 2)
            (try-parse-int-literal (second (node-items s1)))
            (try-parse-int-literal (second (node-items s2))))
       (let* ((d1 (try-parse-int-literal (second (node-items s1))))
              (d2 (try-parse-int-literal (second (node-items s2))))
              (fused (copy-node-shallow s1))
              (num-node (make-node :num (node-pos (second (node-items s2))))))
         (setf (node-s num-node) (format nil "~d" (+ d1 d2))
               (node-items fused) (list (first (node-items s1)) num-node))
         (values (list fused) 2)))
      ;; SORT / SORT_DESC / SORT_BY + TAKE -> TOP / TOP_DESC / TOP_BY
      ((and s2 (string= n2 "TAKE") (= (length (node-items s2)) 2) (sort-step-p s1))
       (let* ((top-name (cond ((string= n1 "SORT") "TOP")
                              ((string= n1 "SORT_DESC") "TOP_DESC")
                              (t "TOP_BY")))
              (fused (make-node :call (node-pos s1))))
         (setf (node-s fused) top-name
               (node-spec fused) (registry-lookup top-name)
               (node-items fused) (append (copy-list (node-items s1))
                                          (list (second (node-items s2)))))
         (values (list fused) 2)))
      ;; FILTER pushdown through MAP: only a predicate over pass-through
      ;; fields that reads neither the whole row nor _K, and only when the
      ;; step after the FILTER renumbers the rows again (spec §7.3: keys are
      ;; part of the value, and a FILTER keeps its input's).
      ((and s2 (string= n1 "MAP") (string= n2 "FILTER") (valid-filter-p s2)
            (let ((f-fields (filter-fields s2)))
              (and f-fields (fields-all-in-p f-fields (map-passthrough-fields s1))))
            (not (multiple-value-bind (binder pred) (filter-body s2)
                   (reads-row-or-key-p pred binder)))
            (keys-renumbered-by-p s3))
       (values (list s2 s1) 2))
      ;; FILTER pushdown through a sort: not a predicate that reads _K, and
      ;; only when a later step renumbers again.
      ((and s2 (sort-step-p s1) (string= n2 "FILTER") (not (step-reads-key-p s2))
            (keys-renumbered-by-p s3))
       (values (list s2 s1) 2))
      ;; FILTER pushdown through SELECT_COLS, under the same key guard.
      ((and s2 (string= n1 "SELECT_COLS") (string= n2 "FILTER") (valid-filter-p s2)
            (let ((f-fields (filter-fields s2)))
              (and f-fields (fields-all-in-p f-fields (select-cols-fields s1))))
            (not (multiple-value-bind (binder pred) (filter-body s2)
                   (reads-row-or-key-p pred binder)))
            (keys-renumbered-by-p s3))
       (values (list s2 s1) 2))
      ;; Sort pushdown through MAP (late materialisation): only a key over
      ;; pass-through fields is the same value before the MAP -- a keyless
      ;; sort compares the MAP's outputs, and a key that reads the whole row
      ;; or _K reads what the MAP changes.
      ((and s2 (string= n1 "MAP")
            (member n2 '("TOP" "TOP_DESC" "TOP_BY" "SORT" "SORT_DESC" "SORT_BY") :test #'string=)
            (map-has-computed-fields-p s1)
            (let ((s-fields (sort-fields s2)))
              (and s-fields (fields-all-in-p s-fields (map-passthrough-fields s1))))
            (not (multiple-value-bind (binder key) (sort-key s2)
                   (reads-row-or-key-p key binder))))
       (values (list s2 s1) 2))
      ;; FILTER + FILTER -> FILTER(p1 AND p2)
      ;; A tentative (pushed) body never fuses with an as-written one: the
      ;; merged body would be one or the other, and each is wrong for the
      ;; other's conjuncts (SEL-0051).
      ((and s2 (string= n1 "FILTER") (string= n2 "FILTER")
            (valid-filter-p s1) (valid-filter-p s2)
            (eq (not (node-tentative (filter-predicate-of s1)))
                (not (node-tentative (filter-predicate-of s2)))))
       (let* ((args1 (node-items s1))
              (args2 (node-items s2))
              (b1 (if (= (length args1) 3) (node-s (second args1)) "_"))
              (pred1 (if (= (length args1) 3) (third args1) (second args1)))
              (b2 (if (= (length args2) 3) (node-s (second args2)) "_"))
              (pred2 (if (= (length args2) 3) (third args2) (second args2)))
              (renamed-pred2 (if (string= b1 b2) pred2 (rename-var-in-node pred2 b2 b1)))
              (and-node (make-node :bin (node-pos pred1)))
              (fused (copy-node-shallow s1)))
         (setf (node-s and-node) "AND"
               (node-l and-node) pred1
               (node-r and-node) renamed-pred2
               (node-tentative and-node) (node-tentative pred1)
               (node-pushed-down and-node) (or (node-pushed-down pred1) (node-pushed-down pred2)))
         (when (node-pushed-down and-node)
           (flet ((rest-of (p) (if (node-pushed-down p) (node-remaining p) p))
                  (true-p (p) (and (eq (node-kind p) :bool) (node-b p))))
             (let ((l (rest-of pred1))
                   (r (rest-of renamed-pred2)))
               (setf (node-remaining and-node)
                     (cond ((true-p l) r)
                           ((true-p r) l)
                           (t (let ((rem (make-node :bin (node-pos pred1))))
                                (setf (node-s rem) "AND"
                                      (node-l rem) l
                                      (node-r rem) r)
                                rem)))))))
         (setf (node-items fused)
               (if (= (length args1) 3)
                   (list (first args1) (second args1) and-node)
                   (list (first args1) and-node)))
         (values (list fused) 2)))
      ;; DEDUPE / DISTINCT twice: the first one did it.
      ((and s2 (member n1 '("DEDUPE" "DISTINCT") :test #'string=)
            (member n2 '("DEDUPE" "DISTINCT") :test #'string=))
       (values (list s1) 2))
      ;; FILTER(TRUE) over a list is the identity: after a step, or over a
      ;; list literal or constructor -- never as the first step over a source
      ;; that might be a scalar.
      ((and (string= n1 "FILTER") (valid-filter-p s1)
            (let ((pred (if (= (length (node-items s1)) 3)
                            (third (node-items s1))
                            (second (node-items s1)))))
              (and pred (eq (node-kind pred) :bool) (node-b pred)))
            (or (plusp i) (source-is-list-p source)))
       (values '() 1))
      (t nil))))

(defun optimize-logical-pipeline-steps (source curr-steps)
  "Tier 1: Engine-agnostic logical relational rewrites on flat pipeline steps,
as one left-to-right sweep over the pairs of adjacent steps, repeated to a
fixed point -- the same sweep, in the same rule order, as the other four
hosts. SOURCE is what the first step reads, which only the FILTER(TRUE) rule
needs."
  (let ((changed t))
    (loop while changed do
      (setf changed nil)
      (let ((new-steps '())
            (i 0)
            (len (length curr-steps)))
        (loop while (< i len) do
          (let ((s1 (nth i curr-steps))
                (s2 (when (< (1+ i) len) (nth (1+ i) curr-steps)))
                (s3 (when (< (+ i 2) len) (nth (+ i 2) curr-steps))))
            (multiple-value-bind (replacement consumed) (logical-step-pair source i s1 s2 s3)
              (if consumed
                  (progn
                    (dolist (r replacement) (push r new-steps))
                    (setf changed t)
                    (incf i consumed))
                  (progn
                    (push s1 new-steps)
                    (incf i 1))))))
        (setf curr-steps (nreverse new-steps))))
    curr-steps))

(defun pass-pushdown-link (curr-steps)
  "Tier 2: In-memory join predicate pushdown through LINK and LINK_LEFT."
  (let ((new-steps '())
        (i 0)
        (len (length curr-steps))
        (changed nil))
    (loop while (< i len) do
      (let ((s1 (nth i curr-steps))
            (s2 (when (< (1+ i) len) (nth (1+ i) curr-steps))))
        (if (and s2 (member (node-s s1) '("LINK" "LINK_LEFT") :test #'string=)
                 (string= (node-s s2) "FILTER")
                 (not (node-pushed-down (filter-predicate-of s2))))
            (let* ((s1-args (node-items s1))
                   (is-inner (string= (node-s s1) "LINK"))
                   (left-src (first s1-args))
                   (right-src (second s1-args))
                   (b1 (cond ((>= (length s1-args) 5) (node-s (third s1-args)))
                             ((eq (node-kind left-src) :var) (node-s left-src))
                             (t "_1")))
                   (b2 (cond ((>= (length s1-args) 5) (node-s (fourth s1-args)))
                             ((eq (node-kind right-src) :var) (node-s right-src))
                             (t "_2")))
                   (left-names (append (list b1 (string-downcase b1) "_1")
                                       (collect-pipeline-source-names left-src)))
                   (right-names (append (list b2 (string-downcase b2) "_2")
                                        (collect-pipeline-source-names right-src)))
                   (s2-args (node-items s2))
                   (f-binder (if (= (length s2-args) 3) (node-s (second s2-args)) "_"))
                   (f-pred (if (= (length s2-args) 3) (third s2-args) (second s2-args)))
                   (conjuncts (split-and-conjuncts f-pred))
                   (left-conjuncts '())
                   (right-conjuncts '())
                   (remaining-conjuncts '()))
              ;; Only the LEADING run of conjuncts that name one side is
              ;; pushed: a conjunct left for the join might raise on a row an
              ;; early test of a later conjunct would drop, and the program as
              ;; written reaches that raise first (spec §7.4; SEL-0051). The
              ;; rest stays for the FILTER above.
              (dolist (c conjuncts)
                (let ((affinity (conjunct-relation-affinity c left-names right-names f-binder)))
                  (cond
                    ((and (null remaining-conjuncts) (eq affinity :left) (null right-conjuncts))
                     (push (rewrite-conjunct-for-relation c left-names f-binder) left-conjuncts))
                    ((and (null remaining-conjuncts) (eq affinity :right) is-inner (null left-conjuncts))
                     (push (rewrite-conjunct-for-relation c right-names f-binder) right-conjuncts))
                    (t
                     (push c remaining-conjuncts)))))
              ;; Pushed conjuncts run tentatively under the LINK (a raise keeps
              ;; the row), and the FILTER after it keeps the whole predicate as
              ;; written, so the value -- including which error is reported --
              ;; is the one the program states (spec §7.4, SEL-0051). The
              ;; retained predicate is marked so the next pass leaves it.
              (if (or left-conjuncts right-conjuncts)
                  (progn
                    ;; If left conjuncts exist, push a FILTER step before LINK
                    (when left-conjuncts
                      (let* ((left-pred (copy-node-shallow (combine-and-conjuncts (nreverse left-conjuncts) (node-pos s2))))
                             (f-spec (registry-lookup "FILTER"))
                             (left-filter (make-node :call (node-pos s2))))
                        (setf (node-tentative left-pred) t)
                        (setf (node-s left-filter) "FILTER"
                              (node-spec left-filter) f-spec
                              (node-items left-filter) (list left-src left-pred))
                        (push left-filter new-steps)))
                    ;; If right conjuncts exist, wrap right relation with FILTER
                    (let ((new-s1 (copy-node-shallow s1)))
                      (when right-conjuncts
                        (let* ((right-pred (copy-node-shallow (combine-and-conjuncts (nreverse right-conjuncts) (node-pos s2))))
                               (f-spec (registry-lookup "FILTER"))
                               (wrapped-r (make-node :call (node-pos s2))))
                          (setf (node-tentative right-pred) t)
                          (setf (node-s wrapped-r) "FILTER"
                                (node-spec wrapped-r) f-spec
                                (node-items wrapped-r) (list right-src right-pred))
                          (let ((new-args (copy-list (node-items new-s1))))
                            (setf (second new-args) wrapped-r
                                  (node-items new-s1) new-args))))
                      (push new-s1 new-steps))
                    ;; The FILTER after the LINK keeps its whole predicate;
                    ;; `remaining` is what it evaluates instead when no
                    ;; tentative body kept a row on an error while its source
                    ;; ran (the pushed conjuncts then held on every row).
                    (let ((kept-pred (copy-node-shallow f-pred))
                          (new-s2 (copy-node-shallow s2)))
                      (setf (node-pushed-down kept-pred) t
                            (node-remaining kept-pred)
                            (if remaining-conjuncts
                                (combine-and-conjuncts (nreverse remaining-conjuncts) (node-pos s2))
                                (let ((true-node (make-node :bool (node-pos s2))))
                                  (setf (node-b true-node) t)
                                  true-node)))
                      (if (= (length s2-args) 3)
                          (setf (node-items new-s2) (list (first s2-args) (second s2-args) kept-pred))
                          (setf (node-items new-s2) (list (first s2-args) kept-pred)))
                      (push new-s2 new-steps))
                    (setf changed t)
                    (incf i 2))
                  (progn
                    (push s1 new-steps)
                    (incf i 1))))
            (progn
              (push s1 new-steps)
              (incf i 1)))))
    (values (nreverse new-steps) changed)))

(defun optimize-inmemory-pipeline-steps (source curr-steps)
  "Tier 2: In-memory physical rewrites, extending Tier 1."
  (let ((changed t))
    (loop while changed do
      (setf changed nil)
      (setf curr-steps (optimize-logical-pipeline-steps source curr-steps))
      (multiple-value-bind (next-steps pass8-changed) (pass-pushdown-link curr-steps)
        (when pass8-changed
          (setf curr-steps next-steps
                changed t)))))
  curr-steps)

(defun optimize-children (node physical depth in-math)
  "A shallow copy of NODE with every child optimised. NODE itself is never
written: the tree a Program owns is the caller's, the other four hosts copy on
the way down, and this one wrote into its input until the cross-language review
-- so a second RUN saw a tree the first had already rewritten, and the SQL
planner saw one the evaluator had rewritten for itself."
  (let ((copy (copy-node-shallow node))
        (next-in-math (is-math-op-p node)))
    (case (node-kind node)
      ((:seq :list)
       (setf (node-items copy)
             (loop for item in (node-items node)
                   collect (optimize-tree item physical (1+ depth) nil))))
      (:call
       (setf (node-items copy)
             (loop for item in (node-items node)
                   collect (optimize-tree item physical (1+ depth) next-in-math))))
      (:bin
       (setf (node-l copy) (optimize-tree (node-l node) physical (1+ depth) next-in-math)
             (node-r copy) (optimize-tree (node-r node) physical (1+ depth) next-in-math)))
      (:index
       (setf (node-l copy) (optimize-tree (node-l node) physical (1+ depth) nil)
             (node-r copy) (optimize-tree (node-r node) physical (1+ depth) nil)))
      (:assign
       (setf (node-r copy) (optimize-tree (node-r node) physical (1+ depth) nil)))
      (:un
       (setf (node-l copy) (optimize-tree (node-l node) physical (1+ depth) next-in-math))))
    copy))

(defun optimize-tree (node physical depth &optional (in-math nil))
  "Tier 1 logical rewrites, plus the Tier 2 physical ones when PHYSICAL.
Anything that is not a node -- the SQL layer's clist, which stage 1 leaves in a
child slot -- is returned as it is: it has no children this walk knows, and a
copy would only be a second object the translator has to recognise."
  (cond
    ((null node) nil)
    ((not (node-p node)) node)
    ;; The evaluator/SQL normaliser owns the public depth error and its source
    ;; position. The entry points never descend into a tree that reaches the
    ;; cap; this guard keeps the walk bounded should a rewrite ever deepen one.
    ((> depth +max-depth+) node)
    ((and (eq (node-kind node) :call)
          (member (node-s node) +pipeline-ops+ :test #'string=))
     (multiple-value-bind (source steps) (unwind-pipeline node)
       (let ((opt-source (optimize-tree source physical depth nil))
             (opt-steps
               (loop for s in steps
                     collect
                     (let ((copy (copy-node-shallow s)))
                       (setf (node-items copy)
                             (cons (first (node-items copy))
                                   (loop for item in (rest (node-items copy))
                                         for index from 1
                                         collect (let ((*fold-constants*
                                                         (and *fold-constants* (step-arg-folds-p s index))))
                                                   (optimize-tree item physical (1+ depth) nil)))))
                       copy))))
         (build-pipeline-ast opt-source
                             (if physical
                                 (optimize-inmemory-pipeline-steps opt-source opt-steps)
                                 (optimize-logical-pipeline-steps opt-source opt-steps))))))
    (t (let* ((copy (optimize-children node physical depth in-math))
              (folded (if *fold-constants* (fold-node copy) copy)))
         (when (and physical (not in-math) (is-math-op-p folded))
           (let ((plan (compile-math-plan folded)))
             (when plan
               (setf (node-math-plan folded) plan))))
         folded))))

(defun exceeds-depth-p (node depth)
  "Whether any node of the tree lies past the evaluator's depth cap, counted
the way the evaluator counts: the root at DEPTH (1 at the entry points), every
child one deeper, an assignment's target excluded (the evaluator walks it
iteratively) -- the same child slots OPTIMIZE-TREE walks. Anything that is not
a node (the SQL layer's clist) has no children. The walk stops at the cap, so
it is bounded however deep the tree is."
  (cond
    ((or (null node) (not (node-p node))) nil)
    ((> depth +max-depth+) t)
    (t (let ((next (1+ depth)))
         (and (case (node-kind node)
                ((:seq :list :call)
                 (some (lambda (item) (exceeds-depth-p item next)) (node-items node)))
                ((:bin :index)
                 (or (exceeds-depth-p (node-l node) next)
                     (exceeds-depth-p (node-r node) next)))
                (:assign (exceeds-depth-p (node-r node) next))
                (:un (exceeds-depth-p (node-l node) next))
                (t nil))
              t)))))

(defun optimize-root (node physical depth)
  "The evaluator is the depth authority (spec §6.4): a tree that reaches the
cap is evaluated as written, so it is returned as written -- the same object.
Raising E_DEPTH here (as this host once did) or folding at the boundary
pre-empts or erases the E_DEPTH the evaluator raises for a chain of 201
additions (each of them foldable), and a rewrite that lifts a child would move
it; not rewriting loses nothing, because such a tree either raises or keeps
its deep part on a branch that is never evaluated."
  (if (exceeds-depth-p node depth)
      node
      (optimize-tree node physical depth)))

(defun optimize-ast-logical (node &optional (depth 1))
  "Applies Tier 1 engine-agnostic logical rewrites to an AST. NODE is not written to."
  (optimize-root node nil depth))

(defun optimize-ast-in-memory (node &optional (depth 1))
  "Applies Tier 1 + Tier 2 in-memory physical rewrites to an AST. NODE is not written to."
  (optimize-root node t depth))

(defun optimize-ast (node)
  (optimize-ast-in-memory node))
