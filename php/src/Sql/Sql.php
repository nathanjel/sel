<?php
// The public interface of the SQL layer. See docs/SQL-TRANSLATION.md §10.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Program;

final class Sql
{
    /**
     * Translate a compiled program into a SQL expression for one dialect.
     * Throws SqlError, whose message is written to be read.
     *
     * Use this when you want to know why a rule cannot be pushed down: during
     * development, in a build-time audit of a rule set, or in a test.
     *
     * @param array<string, array<string,mixed>> $bindings
     * @param array<string,mixed> $options  strict: refuse anything inexact
     */
    public static function translate(Program $program, string $dialect,
                                     array|Bindings $bindings = [], array $options = []): Fragment
    {
        $b = $bindings instanceof Bindings ? $bindings : new Bindings($bindings);
        $t = new Translator($dialect, $b, $options);
        return $t->translate($program->ast);
    }

    /**
     * The same, returning null instead of throwing.
     *
     * Refusal is an expected, ordinary outcome — "this rule cannot be pushed
     * down, evaluate it here instead" — and an expected outcome should not need
     * a try/catch to observe. Only SqlError is caught: a bug in the translator
     * must not be swallowed by the path that exists to handle refusals.
     *
     * @param array<string, array<string,mixed>>|Bindings $bindings
     * @param array<string,mixed> $options
     */
    public static function tryTranslate(Program $program, string $dialect,
                                        array|Bindings $bindings = [], array $options = []): ?Fragment
    {
        try {
            return self::translate($program, $dialect, $bindings, $options);
        } catch (SqlError $e) {
            return null;
        }
    }

    /**
     * Translate a relational pipeline program into a SQL statement fragment.
     *
     * @param array<string, array<string,mixed>>|Bindings $bindings
     * @param array<string,mixed> $options
     */
    public static function translateStatement(Program $program, string $dialect,
                                             array|Bindings $bindings = [], array $options = []): Fragment
    {
        $b = $bindings instanceof Bindings ? $bindings : new Bindings($bindings);
        $t = new Translator($dialect, $b, $options);
        return $t->translateStatement($program->ast);
    }

    /**
     * The same, returning null instead of throwing on SqlError.
     *
     * @param array<string, array<string,mixed>>|Bindings $bindings
     * @param array<string,mixed> $options
     */
    public static function tryTranslateStatement(Program $program, string $dialect,
                                                array|Bindings $bindings = [], array $options = []): ?Fragment
    {
        try {
            return self::translateStatement($program, $dialect, $bindings, $options);
        } catch (SqlError $e) {
            return null;
        }
    }

    /** Every dialect that may be named in a translate() call. @return list<string> */
    public static function dialects(): array
    {
        return Map::targets();
    }
}
