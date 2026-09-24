#!/usr/bin/env python3
"""Form validation -- one rule set, the same verdicts in every host, from Python.

    PYTHONPATH=python python3 examples/validation/python.py

A checkout form's rules, one per field, written in SEL. The server compiles
them once at start-up, so a rule that does not parse fails the deployment
rather than a customer. Each rule answers "" when the field is fine and
ABORT("message") when it is not, so there are two kinds of failure and they
are told apart by code: E_ABORT is a message for the user, anything else means
the rule itself is broken and the user should never see it. dependencies()
tells a browser which rules to re-run when a field changes -- and the browser
runs the very same rule text, in JavaScript.

The four files beside this one print byte-identical output.
"""

import os
import sys

try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'python'))

from sel import SelError, Value, compile                       # noqa: E402

# EXAMPLE-BEGIN rules
RULES = {
    'name':     'IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")',
    'email':    "IF(RMATCH('^[^@ ]+@[^@ ]+\\.[a-z]{2,}$', TRIM(EMAIL), \"i\"), \"\","
                ' ABORT("{EMAIL} does not look like an e-mail address"))',
    'postcode': 'COND(COUNTRY $== "PL" AND NOT RMATCH(\'^\\d{2}-\\d{3}$\', POSTCODE),'
                '       ABORT("Polish postcodes look like 00-000"),'
                '     COUNTRY $== "DE" AND NOT RMATCH(\'^\\d{5}$\', POSTCODE),'
                '       ABORT("German postcodes have five digits"),'
                '     "")',
    'quantity': 'IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,'
                ' ABORT("Choose between 1 and {STOCK}"), "")',
    'total':    'TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);'
                ' IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")',
}
# EXAMPLE-END rules

# 1 - compile once, at start-up ----------------------------------------------------

# EXAMPLE-BEGIN compile
compiled = {field: compile(source) for field, source in RULES.items()}
# EXAMPLE-END compile
print('1. the rule set')
for field, rule in compiled.items():
    print(f'   {field:<9} reads', ' '.join(rule.dependencies()))

# 2 - what to re-check when a field changes ----------------------------------------

print('2. re-check on change')
watch = {}
for field, rule in compiled.items():
    for name in rule.dependencies():
        watch.setdefault(name, []).append(field)
for name in sorted(watch):
    print(f'   {name:<13}', ', '.join(watch[name]))

# 3 - validating submissions -----------------------------------------------------------


# EXAMPLE-BEGIN validate
def validate(form):
    problems = {}
    for field, rule in compiled.items():
        try:
            verdict = rule.run(Value.from_native(form)).as_text()
        except SelError as e:
            # E_ABORT is the rule speaking to the user; anything else is a
            # broken rule or data it cannot read -- log it, show a generic line.
            verdict = e.message if e.code == 'E_ABORT' else f'could not be checked ({e.code})'
        if verdict != '':
            problems[field] = verdict
    return problems
# EXAMPLE-END validate


SUBMISSIONS = [
    {'NAME': 'Anna Nowak', 'EMAIL': 'anna@example.pl', 'COUNTRY': 'PL', 'POSTCODE': '31-874',
     'QTY': '2', 'STOCK': '5', 'PRICE': '19.99', 'DISCOUNT': '0.10', 'CREDIT_LIMIT': '100.00'},
    {'NAME': '   ', 'EMAIL': 'bruno(at)example.de', 'COUNTRY': 'DE', 'POSTCODE': '1011',
     'QTY': '9', 'STOCK': '5', 'PRICE': '19.99', 'DISCOUNT': '0', 'CREDIT_LIMIT': '100.00'},
    {'NAME': 'Chloé', 'EMAIL': 'CHLOE@EXAMPLE.FR ', 'COUNTRY': 'FR', 'POSTCODE': '69002',
     'QTY': '4', 'STOCK': '5', 'PRICE': '29.99', 'DISCOUNT': '0.05', 'CREDIT_LIMIT': '100.00'},
    {'NAME': 'Dawid', 'EMAIL': 'dawid@example.pl', 'COUNTRY': 'PL', 'POSTCODE': '00-950',
     'QTY': '1', 'STOCK': '5', 'PRICE': 'twenty', 'DISCOUNT': '0', 'CREDIT_LIMIT': '100.00'},
]

print('3. submissions')
for n, form in enumerate(SUBMISSIONS, 1):
    problems = validate(form)
    if not problems:
        print(f'   #{n} accepted')
    for field, message in problems.items():
        print(f'   #{n} {field:<9} {message}')
