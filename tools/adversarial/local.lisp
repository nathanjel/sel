(require :asdf)
(push #p"/work/lisp/" asdf:*central-registry*)
(let ((*standard-output* (make-broadcast-stream))) (asdf:load-system :sel-lang))
(with-open-file (in "/work/tools/adversarial/local.sel")
 (loop for s = (read-line in nil) while s do
  (handler-case (write-line (sel:value-dump (sel:run (sel:compile-source s))))
   (sel:sel-error(e)(format t "!~a~%" (sel:sel-error-code e))))))
