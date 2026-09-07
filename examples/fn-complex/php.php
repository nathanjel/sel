<?php
// Goes in register() in the matching php/src/Builtins/*.php. Not a runnable file: this is a fragment that
// compiles only in place. See README.md beside it.
// EXAMPLE-BEGIN
Registry::define(['name' => 'FIRST', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
    'fn' => static function (Args $a, Context $ctx): Value {
        $three = $a->count() === 3;
        $binder = $three ? $a->symbol(1) : '_';
        $body = $a->node($three ? 2 : 1);

        $list = $a->val(0);
        $items = $list->size() > 0 ? $list->entries()
            : ($list->kind === Value::NONE ? [] : [['1', $list]]);

        foreach ($items as [$key, $item]) {
            $ctx->pushFrame([$binder => $item, '_K' => Value::text($key)]);
            try {
                if ($a->evalNode($body)->asBool($body['pos'])) {
                    return $item->copy();
                }
            } finally {
                $ctx->popFrame();
            }
        }
        return Value::text('');
    }]);
// EXAMPLE-END
