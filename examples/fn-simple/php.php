<?php
// Goes inside register() in the matching php/src/Builtins/*.php. Not a runnable
// file: this fragment compiles only in place.
// EXAMPLE-BEGIN
Registry::define(['name' => 'ORD_SUFFIX', 'min' => 1, 'max' => 1,
    'fn' => static function (Args $a): Value {
        $n = $a->nonNegInt(0);
        $tens = $n % 100;
        if ($tens >= 11 && $tens <= 13) {
            return Value::text("{$n}th");
        }
        return Value::text($n . match ($n % 10) { 1 => 'st', 2 => 'nd', 3 => 'rd', default => 'th' });
    }]);
// EXAMPLE-END
