<?php
// A realistic backend integration: a table of rules compiled once, run against a
// payload, producing field-level messages.
//
//   php examples/integration-php.php

declare(strict_types=1);

require_once __DIR__ . '/../php/src/bootstrap.php';

use Sel\Program;
use Sel\Sel;
use Sel\SelError;
use Sel\Value;

/**
 * Compile once, run per request. A Program is immutable and reusable, so in a
 * real app this is built at boot (or cached by rule hash) and never rebuilt.
 */
final class RuleSet
{
    /** @var array<string, Program> */
    private array $rules = [];

    /** @param array<string, string> $sources field => SEL source */
    public function __construct(array $sources)
    {
        foreach ($sources as $field => $source) {
            // A syntax error here is a deployment problem, not a user problem —
            // it surfaces at boot rather than on someone's checkout page.
            $this->rules[$field] = Sel::compile($source);
        }
    }

    /**
     * Runs every rule against one payload. A rule yields "" when the field is
     * fine, or a message when it is not.
     *
     * @param array<string, mixed> $payload
     * @return array<string, string> field => message, only for failures
     */
    public function validate(array $payload): array
    {
        $messages = [];
        foreach ($this->rules as $field => $program) {
            // Each rule gets its own context: rules must not see each other's
            // intermediate variables.
            $context = Value::fromNative($payload);
            try {
                $result = $program->run($context)->asText();
            } catch (SelError $e) {
                $result = self::present($field, $e);
            }
            if ($result !== '') {
                $messages[$field] = $result;
            }
        }
        return $messages;
    }

    /**
     * E_ABORT is the author saying "tell the user this". Every other code means
     * the rule itself is broken — log it, and do not leak it to the user.
     */
    private static function present(string $field, SelError $e): string
    {
        if ($e->code === 'E_ABORT') {
            return $e->getMessage();
        }
        error_log("SEL rule for {$field} failed: {$e}");
        return 'could not be validated';
    }

    /**
     * Which rules read a given field. Send this to the client and it knows what
     * to re-run when that input changes.
     *
     * @return array<string, list<string>> field => rules that read it
     */
    public function watchMap(): array
    {
        $map = [];
        foreach ($this->rules as $field => $program) {
            foreach ($program->dependencies() as $input) {
                $map[$input][] = $field;
            }
        }
        ksort($map);
        return $map;
    }
}

// --- the rules ---------------------------------------------------------------

$rules = new RuleSet([
    'email' => <<<'SEL'
        COND(TRIM(EMAIL) $== "",                          "Email is required.",
             NOT RMATCH('^[^@ ]+@[^@ .]+\.[^@ ]+$', EMAIL), "That does not look like an email address.",
             LEN(EMAIL) > 254,                            "Email is too long.",
                                                          "")
        SEL,

    'postcode' => <<<'SEL'
        COND(TRIM(POSTCODE) $== "",                    "Postcode is required.",
             NOT RMATCH('^\d{2}-\d{3}$', POSTCODE),    "Use the format 12-345.",
                                                      "")
        SEL,

    'order' => <<<'SEL'
        TOTAL = SUM(ITEMS, _["QTY"] * _["PRICE"]);
        COND(COUNT(ITEMS) == 0,                "Your basket is empty.",
             ANY(ITEMS, _["QTY"] <= 0),        "Every line needs a quantity.",
             TOTAL > CREDIT_LIMIT,             "Total {TOTAL} exceeds your limit of {CREDIT_LIMIT}.",
                                               "")
        SEL,
]);

// --- a request ---------------------------------------------------------------

$payload = [
    'EMAIL' => 'anna@example',
    'POSTCODE' => '31-874',
    'CREDIT_LIMIT' => '50.00',
    'ITEMS' => [
        ['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99'],
        ['SKU' => 'CD-5678', 'QTY' => '0', 'PRICE' => '5.00'],
    ],
];

echo "messages:\n";
foreach ($rules->validate($payload) as $field => $message) {
    echo "  {$field}: {$message}\n";
}

$payload['EMAIL'] = 'anna@example.com';
$payload['ITEMS'][1]['QTY'] = '1';
echo "after fixing email and quantity:\n";
$messages = $rules->validate($payload);
echo $messages ? '  ' . json_encode($messages) . "\n" : "  (none)\n";

echo "watch map (field => rules to re-run):\n";
foreach ($rules->watchMap() as $input => $affected) {
    printf("  %-13s %s\n", $input, implode(', ', $affected));
}
