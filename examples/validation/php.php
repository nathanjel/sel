<?php
// Form validation — one rule set, the same verdicts in every host, from PHP.
//
//   php examples/validation/php.php
//
// A checkout form's rules, one per field, written in SEL. The server compiles
// them once at start-up, so a rule that does not parse fails the deployment
// rather than a customer. Each rule answers "" when the field is fine and
// ABORT("message") when it is not, so there are two kinds of failure and they
// are told apart by code: E_ABORT is a message for the user, anything else means
// the rule itself is broken and the user should never see it. dependencies()
// tells a browser which rules to re-run when a field changes — and the browser
// runs the very same rule text, in JavaScript.
//
// The four files beside this one print byte-identical output.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

// EXAMPLE-BEGIN rules
const RULES = [
    'name'     => 'IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")',
    'email'    => 'IF(RMATCH(\'^[^@ ]+@[^@ ]+\.[a-z]{2,}$\', TRIM(EMAIL), "i"), "",'
                . ' ABORT("{EMAIL} does not look like an e-mail address"))',
    'postcode' => 'COND(COUNTRY $== "PL" AND NOT RMATCH(\'^\d{2}-\d{3}$\', POSTCODE),'
                . '       ABORT("Polish postcodes look like 00-000"),'
                . '     COUNTRY $== "DE" AND NOT RMATCH(\'^\d{5}$\', POSTCODE),'
                . '       ABORT("German postcodes have five digits"),'
                . '     "")',
    'quantity' => 'IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,'
                . ' ABORT("Choose between 1 and {STOCK}"), "")',
    'total'    => 'TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);'
                . ' IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")',
];
// EXAMPLE-END rules

// 1 — compile once, at start-up ----------------------------------------------------

// EXAMPLE-BEGIN compile
$compiled = array_map(fn (string $source) => Sel::compile($source), RULES);
// EXAMPLE-END compile
echo "1. the rule set\n";
foreach ($compiled as $field => $rule) {
    printf("   %-9s reads %s\n", $field, implode(' ', $rule->dependencies()));
}

// 2 — what to re-check when a field changes ----------------------------------------

echo "2. re-check on change\n";
$watch = [];
foreach ($compiled as $field => $rule) {
    foreach ($rule->dependencies() as $name) {
        $watch[$name][] = $field;
    }
}
ksort($watch, SORT_STRING);
foreach ($watch as $name => $fields) {
    printf("   %-13s %s\n", $name, implode(', ', $fields));
}

// 3 — validating submissions -----------------------------------------------------------

// EXAMPLE-BEGIN validate
function validate(array $compiled, array $form): array
{
    $problems = [];
    foreach ($compiled as $field => $rule) {
        try {
            $verdict = $rule->run(Value::fromNative($form))->asText();
        } catch (SelError $e) {
            // E_ABORT is the rule speaking to the user; anything else is a
            // broken rule or data it cannot read — log it, show a generic line.
            $verdict = $e->code === 'E_ABORT' ? $e->getMessage() : "could not be checked ({$e->code})";
        }
        if ($verdict !== '') {
            $problems[$field] = $verdict;
        }
    }
    return $problems;
}
// EXAMPLE-END validate

const SUBMISSIONS = [
    ['NAME' => 'Anna Nowak', 'EMAIL' => 'anna@example.pl', 'COUNTRY' => 'PL', 'POSTCODE' => '31-874',
     'QTY' => '2', 'STOCK' => '5', 'PRICE' => '19.99', 'DISCOUNT' => '0.10', 'CREDIT_LIMIT' => '100.00'],
    ['NAME' => '   ', 'EMAIL' => 'bruno(at)example.de', 'COUNTRY' => 'DE', 'POSTCODE' => '1011',
     'QTY' => '9', 'STOCK' => '5', 'PRICE' => '19.99', 'DISCOUNT' => '0', 'CREDIT_LIMIT' => '100.00'],
    ['NAME' => 'Chloé', 'EMAIL' => 'CHLOE@EXAMPLE.FR ', 'COUNTRY' => 'FR', 'POSTCODE' => '69002',
     'QTY' => '4', 'STOCK' => '5', 'PRICE' => '29.99', 'DISCOUNT' => '0.05', 'CREDIT_LIMIT' => '100.00'],
    ['NAME' => 'Dawid', 'EMAIL' => 'dawid@example.pl', 'COUNTRY' => 'PL', 'POSTCODE' => '00-950',
     'QTY' => '1', 'STOCK' => '5', 'PRICE' => 'twenty', 'DISCOUNT' => '0', 'CREDIT_LIMIT' => '100.00'],
];

echo "3. submissions\n";
foreach (SUBMISSIONS as $i => $form) {
    $n = $i + 1;
    $problems = validate($compiled, $form);
    if (!$problems) {
        echo "   #$n accepted\n";
    }
    foreach ($problems as $field => $message) {
        printf("   #%d %-9s %s\n", $n, $field, $message);
    }
}
