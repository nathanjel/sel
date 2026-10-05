<?php
// The operator vocabulary, once (spec/lexicon.json, rendered into Lexicon.php).
//
// Every "is this a comparison, an arithmetic operator, a short-circuit?"
// question this host asks -- in the parser, the evaluator, the constant folder,
// the optimiser, the join pre-filter, the dependency walker and the SQL layer --
// is answered from the tables below, which are built from the lexicon when this
// file is loaded. A native dispatch keyed by operator (the evaluator's switch)
// declares what it handles and is checked against the lexicon here, so an
// operator added to the lexicon and forgotten there stops the library loading
// instead of failing at run time.

declare(strict_types=1);

namespace Sel;

final class Ops
{
    /** @var array<string, array<string,mixed>> infix token => its Lexicon::OPS row */
    public static array $infix = [];

    /** @var array<string, array<string,mixed>> prefix token => its Lexicon::OPS row */
    public static array $prefix = [];

    /** @var array<string, array<string,true>> family => its infix tokens */
    public static array $family = [];

    /** @var array<string,string> compound assignment => the binary operator it applies */
    public static array $compound = [];

    /** @var array<string,true> binary operators whose right operand may never run */
    public static array $shortCircuit = [];

    /** @var array<string,true> the operators that build a binary node */
    public static array $binary = [];

    public static function init(): void
    {
        foreach (Lexicon::OPS as $op) {
            $token = $op['token'];
            if ($op['fixity'] === 'infix') {
                self::$infix[$token] = $op;
                self::$family[$op['family']][$token] = true;
                if ($op['node'] === 'bin') {
                    self::$binary[$token] = true;
                }
            } elseif ($op['fixity'] === 'prefix') {
                self::$prefix[$token] = $op;
            }
            if ($op['compound'] !== null) {
                self::$compound[$token] = $op['compound'];
            }
            if ($op['shortCircuit']) {
                self::$shortCircuit[$token] = true;
            }
        }
    }

    public static function inFamily(string $op, string $family): bool
    {
        return isset(self::$family[$family][$op]);
    }

    /**
     * A native per-operator dispatch must handle exactly the lexicon's binary
     * operators: one it lacks would fail when first evaluated, and one the
     * lexicon lacks is dead. Checked when the dispatching file loads.
     *
     * @param list<string> $handled
     */
    public static function requireHandles(string $where, array $handled): void
    {
        $missing = array_diff(array_keys(self::$binary), $handled);
        $extra = array_diff($handled, array_keys(self::$binary));
        if ($missing !== [] || $extra !== []) {
            throw new \LogicException("{$where} disagrees with spec/lexicon.json: "
                . ($missing !== [] ? 'does not handle ' . implode(' ', $missing) : '')
                . ($missing !== [] && $extra !== [] ? '; ' : '')
                . ($extra !== [] ? 'handles unknown ' . implode(' ', $extra) : ''));
        }
    }
}

Ops::init();
