<?php
// Control, structure and aggregate built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Context;
use Sel\Dec;
use Sel\Registry;
use Sel\Value;

use function Sel\fail;

final class Core
{
    public static function register(): void
    {
        // The whole of SEL's control flow. Lazy, so only the taken branch is
        // evaluated — exactly the property the AST calling convention provides.
        Registry::define(['name' => 'IF', 'min' => 2, 'max' => 3, 'lazy' => true,
            'fn' => static function (Args $a): Value {
                if ($a->bool(0)) {
                    return $a->val(1);
                }
                return $a->count() === 3 ? $a->val(2) : Value::text('');
            }]);

        // Flat multi-branch selection — sugar for a nested IF ladder, with exactly
        // the same laziness: conditions are evaluated in order, and only the
        // result that matches is evaluated at all.
        //
        // The argument count must be odd: condition/result pairs plus a mandatory
        // default. IF can safely let its two-argument form default to "" because
        // there is one branch and nothing to mis-pair, but with an even count here
        // a single miscounted comma would shift every pair by one and still
        // compile. Requiring the default turns that into a compile-time E_ARITY
        // instead of a wrong answer.
        Registry::define(['name' => 'COND', 'min' => 3, 'max' => PHP_INT_MAX, 'lazy' => true,
            'arityError' => static fn (int $n): ?string => $n % 2 === 0
                ? 'COND takes condition/result pairs and a final default '
                    . "(an odd number of arguments), got {$n}"
                : null,
            'fn' => static function (Args $a): Value {
                $last = $a->count() - 1;
                for ($i = 0; $i < $last; $i += 2) {
                    if ($a->bool($i)) {
                        return $a->val($i + 1);
                    }
                }
                return $a->val($last);
            }]);

        // The one error a rule author raises deliberately.
        Registry::define(['name' => 'ABORT', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                fail('E_ABORT', $a->text(0), $a->posOf(0));
            }]);

        Registry::define(['name' => 'COUNT', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::int($a->val(0)->size())]);

        Registry::define(['name' => 'INDEXES', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::list(
                array_map(static fn (string $k): Value => Value::text($k), $a->val(0)->keys()),
            )]);

        Registry::define(['name' => 'HAS', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::bool($a->val(0)->has($a->text(1)))]);

        self::registerAggregates();
    }

    /**
     * Two-argument form binds `_`; three-argument form takes a bare identifier as
     * the binder, checked by inspecting the AST node the caller handed us.
     *
     * @return array{binder:string, body:array<string,mixed>}
     */
    private static function shape(Args $a): array
    {
        return $a->count() === 3
            ? ['binder' => $a->symbol(1), 'body' => $a->node(2)]
            : ['binder' => '_', 'body' => $a->node(1)];
    }

    /**
     * A scalar with no children behaves as a one-element list containing itself,
     * consistent with scalar context (§3.2). A NONE with no children is genuinely
     * empty — that is what FILTER returns when nothing matched, and ALL over it
     * must be TRUE rather than a scalar-context failure.
     *
     * @return list<array{0:string,1:Value}>
     */
    private static function elements(Value $value): array
    {
        if ($value->size() > 0) {
            return $value->entries();
        }
        return $value->kind === Value::NONE ? [] : [['1', $value]];
    }

    /**
     * Runs $visit per element with the binder and _K in scope. Returning a Value
     * from $visit stops the walk and becomes the result.
     */
    private static function walk(Args $a, Context $ctx, callable $visit): ?Value
    {
        ['binder' => $binder, 'body' => $body] = self::shape($a);
        foreach (self::elements($a->val(0)) as [$key, $item]) {
            $ctx->pushFrame([$binder => $item, '_K' => Value::text($key)]);
            try {
                $result = $visit($a->evalNode($body), $key, $item, $body);
            } finally {
                $ctx->popFrame();
            }
            if ($result !== null) {
                return $result;
            }
        }
        return null;
    }

    private static function registerAggregates(): void
    {
        Registry::define(['name' => 'ALL', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $short = self::walk($a, $ctx, static fn (Value $r, $k, $i, array $body): ?Value =>
                    $r->asBool($body['pos']) ? null : Value::bool(false));
                return $short ?? Value::bool(true);
            }]);

        Registry::define(['name' => 'ANY', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $short = self::walk($a, $ctx, static fn (Value $r, $k, $i, array $body): ?Value =>
                    $r->asBool($body['pos']) ? Value::bool(true) : null);
                return $short ?? Value::bool(false);
            }]);

        Registry::define(['name' => 'MAP', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $out = [];
                self::walk($a, $ctx, static function (Value $r) use (&$out): ?Value {
                    $out[] = $r->copy();
                    return null;
                });
                return Value::list($out);
            }]);

        // The one aggregate that preserves keys — a filtered list should still be
        // addressable the way the original was.
        Registry::define(['name' => 'FILTER', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $out = Value::none();
                self::walk($a, $ctx, static function (Value $r, string $key, Value $item, array $body) use ($out): ?Value {
                    if ($r->asBool($body['pos'])) {
                        $out->set($key, $item->copy());
                    }
                    return null;
                });
                return $out;
            }]);

        Registry::define(['name' => 'SUM', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $total = Dec::zero();
                self::walk($a, $ctx, static function (Value $r, $k, $i, array $body) use (&$total): ?Value {
                    $total = Dec::add($total, $r->asDecimal($body['pos']));
                    return null;
                });
                return Value::num($total);
            }]);

        // Strict, not an aggregate: its second argument is a separator, not a body.
        Registry::define(['name' => 'JOIN', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $sep = $a->text(1);
                $parts = [];
                foreach (self::elements($a->val(0)) as [, $item]) {
                    $parts[] = $item->asText($a->posOf(0));
                }
                return Value::text(implode($sep, $parts));
            }]);
    }
}
