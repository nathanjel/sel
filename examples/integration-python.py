#!/usr/bin/env python3
"""A realistic backend integration: a table of rules compiled once, run against a
payload, producing field-level messages.

  python3 examples/integration-python.py

Mirrors examples/integration-php.php and examples/integration-js.mjs, and prints
identical output.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sel as _sel_probe                                    # noqa: F401
    if not hasattr(_sel_probe, 'compile'):
        raise ImportError
except (ImportError, AttributeError):
    sys.path.insert(0, os.path.join(HERE, '..', 'python'))
    if 'sel' in sys.modules and not hasattr(sys.modules['sel'], 'compile'):
        del sys.modules['sel']

from sel import Program, SelError, Value, compile               # noqa: E402


class RuleSet:
    """Compile once, run per request. A Program is immutable and reusable, so in a
    real app this is built at boot (or cached by rule hash) and never rebuilt.
    """

    def __init__(self, sources: dict[str, str]) -> None:
        self.rules: dict[str, Program] = {}
        for field, source in sources.items():
            # A syntax error here is a deployment problem, not a user problem —
            # it surfaces at boot rather than on someone's checkout page.
            self.rules[field] = compile(source)

    def validate(self, payload: dict[str, Any], only: list[str] | set[str] | None = None) -> dict[str, str]:
        """Runs every rule against one payload. A rule yields "" when the field is
        fine, or a message when it is not.
        """
        messages: dict[str, str] = {}
        for field, program in self.rules.items():
            if only is not None and field not in only:
                continue
            # Each rule gets its own context: rules must not see each other's
            # intermediate variables.
            context = Value.from_native(payload)
            try:
                result = program.run(context).as_text()
            except SelError as e:
                result = self.present(field, e)
            if result != '':
                messages[field] = result
        return messages

    @staticmethod
    def present(field: str, e: SelError) -> str:
        """E_ABORT is the author saying "tell the user this". Every other code means
        the rule itself is broken — log it, and do not leak it to the user.
        """
        if e.code == 'E_ABORT':
            return e.message
        print(f"SEL rule for {field} failed: {e}", file=sys.stderr)
        return 'could not be validated'

    def watch_map(self) -> dict[str, list[str]]:
        """Which rules read a given field. Send this to the client and it knows what
        to re-run when that input changes.
        """
        map_: dict[str, list[str]] = {}
        for field, program in self.rules.items():
            for input_name in program.dependencies():
                map_.setdefault(input_name, []).append(field)
        return dict(sorted(map_.items()))

    watchMap = watch_map


# --- the rules, identical to examples/integration-php.php --------------------

rules = RuleSet({
    'email': """
        COND(TRIM(EMAIL) $== "",                            "Email is required.",
             NOT RMATCH('^[^@ ]+@[^@ .]+\\.[^@ ]+$', EMAIL), "That does not look like an email address.",
             LEN(EMAIL) > 254,                              "Email is too long.",
                                                            "")""",

    'postcode': """
        COND(TRIM(POSTCODE) $== "",                 "Postcode is required.",
             NOT RMATCH('^\\d{2}-\\d{3}$', POSTCODE), "Use the format 12-345.",
                                                    "")""",

    'order': """
        TOTAL = SUM(ITEMS, _["QTY"] * _["PRICE"]);
        COND(COUNT(ITEMS) == 0,          "Your basket is empty.",
             ANY(ITEMS, _["QTY"] <= 0),  "Every line needs a quantity.",
             TOTAL > CREDIT_LIMIT,       "Total {TOTAL} exceeds your limit of {CREDIT_LIMIT}.",
                                         "")""",
})

# --- a request ---------------------------------------------------------------

payload = {
    'EMAIL': 'anna@example',
    'POSTCODE': '31-874',
    'CREDIT_LIMIT': '50.00',
    'ITEMS': [
        {'SKU': 'AB-1234', 'QTY': '3', 'PRICE': '19.99'},
        {'SKU': 'CD-5678', 'QTY': '0', 'PRICE': '5.00'},
    ],
}

print('messages:')
for field, message in rules.validate(payload).items():
    print(f"  {field}: {message}")

payload['EMAIL'] = 'anna@example.com'
payload['ITEMS'][1]['QTY'] = '1'
print('after fixing email and quantity:')
messages = rules.validate(payload)
print(f"  {json.dumps(messages, separators=(',', ':'))}" if messages else '  (none)')

print('watch map (field => rules to re-run):')
watch = rules.watch_map()
for input_name, affected in watch.items():
    print(f"  {input_name:<13} {', '.join(affected)}")

# Only the affected rules re-run when one input changes.
print('EMAIL changed, so re-running:', ', '.join(watch['EMAIL']))
print('  ', json.dumps(rules.validate(payload, watch['EMAIL']), separators=(',', ':')))
