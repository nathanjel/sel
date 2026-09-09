<?php
// Null and safety built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Registry;
use Sel\Value;

final class NullOps
{
    public static function register(): void
    {
        Registry::define([
            'name' => 'IS_NULL', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::bool($a->val(0)->isNull()),
        ]);

        Registry::define([
            'name' => 'IS_NOT_NULL', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::bool(!$a->val(0)->isNull()),
        ]);

        Registry::define([
            'name' => 'COALESCE', 'min' => 1, 'max' => PHP_INT_MAX, 'lazy' => true,
            'fn' => static function (Args $a): Value {
                $n = $a->count();
                for ($i = 0; $i < $n; $i++) {
                    $v = $a->val($i);
                    if (!$v->isNull()) {
                        return $v;
                    }
                }
                return Value::null();
            },
        ]);

        Registry::define([
            'name' => 'GET', 'min' => 2, 'max' => 3, 'lazy' => true,
            'fn' => static function (Args $a): Value {
                $target = $a->val(0);
                $key = $a->text(1);
                if (!$target->isNull() && $target->has($key)) {
                    $val = $target->get($key);
                    if ($val !== null) {
                        return $val;
                    }
                }
                if ($a->count() > 2) {
                    return $a->val(2);
                }
                return Value::null();
            },
        ]);

        Registry::define([
            'name' => 'PATH', 'min' => 2, 'max' => 3, 'lazy' => true,
            'fn' => static function (Args $a): Value {
                $target = $a->val(0);
                $pathStr = $a->text(1);
                if ($pathStr === '') {
                    return $target;
                }
                $segments = explode('.', $pathStr);
                $cur = $target;
                foreach ($segments as $seg) {
                    if ($cur->isNull() || !$cur->has($seg)) {
                        if ($a->count() > 2) {
                            return $a->val(2);
                        }
                        return Value::null();
                    }
                    $cur = $cur->get($seg);
                    if ($cur === null) {
                        if ($a->count() > 2) {
                            return $a->val(2);
                        }
                        return Value::null();
                    }
                }
                return $cur;
            },
        ]);

        Registry::define([
            'name' => 'IS_BLANK', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::bool($a->val(0)->isVacuous()),
        ]);

        Registry::define([
            'name' => 'IS_PRESENT', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::bool(!$a->val(0)->isVacuous()),
        ]);
    }
}
