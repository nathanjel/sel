;;;; The public interface. See spec/SPEC.md §8.

(defpackage #:sel
  (:use #:common-lisp)
  (:export
   ;; Errors
   #:sel-error
   #:sel-error-code
   #:sel-error-message
   #:sel-error-line
   #:sel-error-col
   #:sel-error-offset

   ;; Values
   #:value
   #:value-p
   #:value-kind
   #:make-none
   #:make-text
   #:make-bin
   #:make-bool
   #:make-num
   #:make-int
   #:make-list-value
   #:value-size
   #:value-has
   #:value-get
   #:value-set
   #:value-keys
   #:value-values
   #:value-entries
   #:value-copy
   #:value-eql
   #:value-dump
   #:as-text
   #:as-bytes
   #:as-bool
   #:as-num
   #:looks-numeric
   #:from-native
   #:to-native

   ;; Programs
   #:program
   #:program-p
   #:program-source
   #:compile-source
   #:run
   #:dependencies
   #:evaluate
   #:function-names))
