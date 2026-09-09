;;;; Built-ins for NULL handling, navigation, and blanks.

(in-package #:sel)

(define-builtin "IS_NULL" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-bool (value-null-p (args-val a 0)))))

(define-builtin "IS_NOT_NULL" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-bool (not (value-null-p (args-val a 0))))))

(define-builtin "COALESCE" 1 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (loop for i from 0 below (args-count a)
          for v = (args-val a i)
          unless (value-null-p v)
            return v
          finally (return (make-null))))
  :lazy t)

(define-builtin "GET" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((target (args-val a 0))
          (key (args-text a 1)))
      (if (and (not (value-null-p target)) (value-has target key))
          (let ((val (value-get target key)))
            (if val
                val
                (if (> (args-count a) 2)
                    (args-val a 2)
                    (make-null))))
          (if (> (args-count a) 2)
              (args-val a 2)
              (make-null)))))
  :lazy t)

(define-builtin "PATH" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((target (args-val a 0))
          (path-str (args-text a 1)))
      (if (string= path-str "")
          target
          (let ((segments (loop for start = 0 then (1+ pos)
                                for pos = (position #\. path-str :start start)
                                collect (subseq path-str start pos)
                                while pos))
                (cur target))
            (dolist (seg segments cur)
              (if (and (not (value-null-p cur)) (value-has cur seg))
                  (let ((val (value-get cur seg)))
                    (if val
                        (setf cur val)
                        (return (if (> (args-count a) 2)
                                    (args-val a 2)
                                    (make-null)))))
                  (return (if (> (args-count a) 2)
                              (args-val a 2)
                              (make-null)))))))))
  :lazy t)

(define-builtin "IS_BLANK" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-bool (value-vacuous-p (args-val a 0)))))

(define-builtin "IS_PRESENT" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-bool (not (value-vacuous-p (args-val a 0))))))
