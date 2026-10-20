#!/usr/bin/env bash
# The PHP package, installed the way `composer require nathanjel/sel-lang` installs it.
#
# Every other PHP lane requires php/src/bootstrap.php, which loads the whole
# language up front, so none of them sees what an application using Composer
# gets instead. composer.json's autoload is a classmap of php/src (the SQL layer
# and bootstrap.php left out of it) plus two files loaded on every request:
# SelError.php, whose functions and constant no classmap can load, and
# Sql/autoload.php, the SQL layer's own loader. The Registry registers the
# shipped modules the first time the table is read (Registry::boot()). Nothing
# here imitates Composer: this stages what Packagist's dist of this tree carries
# for PHP (composer.json and php/ as git would commit them, less what
# .gitattributes export-ignores), installs it into an empty project through a
# path repository with Composer itself, offline, and from that project alone:
#
#   - requiring vendor/autoload.php loads SelError.php, the Limits it is defined
#     from and Sql/autoload.php, nothing else, and defines every function and
#     constant php/src/bootstrap.php defines;
#   - evaluating does not load the SQL layer; naming a Sel\Sql class loads it and
#     it translates; every class the two bootstraps declare is the autoloader's
#     to find, from the installed copy;
#   - the same with --classmap-authoritative, the production setting, where a
#     Sel\Sql class must still fall through to the package's own loader;
#   - Registry::define() before anything has evaluated: a shipped name is
#     refused, as it is after bootstrap.php, and the table stays whole;
#   - the conformance suite, through the autoloader instead of bootstrap.php;
#   - vendor/bin/sel, the bin composer.json declares.
#
#   tools/check-php-package.sh        needs composer (SEL_COMPOSER names another)
#   SEL_SKIP_PHP_PACKAGE=1            opts out

set -uo pipefail
cd "$(dirname "$0")/.."

if [ "${SEL_SKIP_PHP_PACKAGE:-}" = "1" ]; then
  echo "php package: skipped (SEL_SKIP_PHP_PACKAGE=1)"
  exit 0
fi
COMPOSER_BIN="${SEL_COMPOSER:-composer}"
if ! command -v "$COMPOSER_BIN" >/dev/null 2>&1; then
  echo "php package: composer is not installed; install it, name it in SEL_COMPOSER, or set SEL_SKIP_PHP_PACKAGE=1" >&2
  exit 1
fi

ROOT="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
status=0
fail() { echo "FAIL php package: $*"; status=1; }

# Composer on its own: no global configuration, no cache, no network (packagist.org
# is off below), no plugins, no scripts.
export COMPOSER_HOME="$WORK/composer-home" COMPOSER_CACHE_DIR="$WORK/composer-cache" \
  COMPOSER_NO_INTERACTION=1 COMPOSER_NO_AUDIT=1 COMPOSER_DISABLE_XDEBUG_WARN=1
unset COMPOSER COMPOSER_VENDOR_DIR COMPOSER_BIN_DIR
run_composer() { "$COMPOSER_BIN" --no-plugins --no-scripts --no-ansi "$@"; }

# 1. What the dist carries for PHP: tracked files and new ones git does not ignore,
#    less what git archive leaves out (export-ignore) and what is deleted.
pkg="$WORK/package"
mkdir -p "$pkg"
git ls-files -z --cached --others --exclude-standard -- composer.json LICENSE README.md php \
  | git check-attr -z --stdin export-ignore \
  | while IFS= read -r -d '' path && IFS= read -r -d '' _attr && IFS= read -r -d '' value; do
      [ "$value" = set ] || [ ! -e "$path" ] || printf '%s\0' "$path"
    done \
  | xargs -0 -r tar -cf - --no-recursion | tar -xf - -C "$pkg"
[ -f "$pkg/composer.json" ] && [ -f "$pkg/php/src/bootstrap.php" ] \
  || { fail "nothing staged from composer.json and php/"; exit 1; }

# 2. An application that requires it, from that copy only: mirrored, as an install
#    from the dist copies (a symlink would leave the files where they are), and at
#    whatever dev version Composer gives a directory with no git in it.
app="$WORK/app"
mkdir -p "$app"
cat > "$app/composer.json" <<EOF
{
  "name": "sel-check/application",
  "type": "project",
  "repositories": [
    {"type": "path", "url": "$pkg", "options": {"symlink": false}},
    {"packagist.org": false}
  ],
  "require": {"nathanjel/sel-lang": "*@dev"}
}
EOF
if ! run_composer install -d "$app" --no-progress > "$WORK/install.log" 2>&1; then
  fail "composer install failed"
  tail -20 "$WORK/install.log" | sed 's/^/       /'
  exit 1
fi
inst="$app/vendor/nathanjel/sel-lang"
[ -f "$inst/php/src/bootstrap.php" ] || { fail "composer installed no $inst/php/src/bootstrap.php"; exit 1; }

# 3. The probe: one fresh process per scenario, as a request is.
cat > "$app/probe.php" <<'PHP'
<?php
// What an application gets from `require 'vendor/autoload.php'`, in a fresh process.
declare(strict_types=1);

$scenario = $argv[1] ?? '';
$pkg = (string) realpath(__DIR__ . '/vendor/nathanjel/sel-lang');
$failures = [];
$check = static function (bool $ok, string $what) use (&$failures): void {
    if (!$ok) $failures[] = $what;
};

// What the bootstrap defines, from the installed copy, in a process of its own: the
// language (bootstrap.php), then what the SQL layer adds (Sql/bootstrap.php).
$baseline = json_decode((string) shell_exec(escapeshellarg(PHP_BINARY) . ' -r ' . escapeshellarg(
    '$sel = static fn (array $names): array => array_values(array_filter($names,'
    . ' static fn (string $n): bool => stripos($n, "sel\\\\") === 0));'
    . '$defs = static fn (): array => ['
    . ' "classes" => $sel(array_merge(get_declared_classes(), get_declared_interfaces(), get_declared_traits())),'
    . ' "functions" => $sel(get_defined_functions()["user"]),'
    . ' "constants" => $sel(array_keys(get_defined_constants()))];'
    . 'require ' . var_export($pkg . '/php/src/bootstrap.php', true) . '; $language = $defs();'
    . 'require ' . var_export($pkg . '/php/src/Sql/bootstrap.php', true) . '; $all = $defs();'
    . '$sql = []; foreach ($all as $k => $v) $sql[$k] = array_values(array_diff($v, $language[$k]));'
    . 'echo json_encode(["language" => $language, "sql" => $sql]);')), true);
if (!is_array($baseline) || ($baseline['language']['classes'] ?? []) === [] || ($baseline['sql']['classes'] ?? []) === []) {
    fwrite(STDERR, "FAIL cannot list what php/src/bootstrap.php and Sql/bootstrap.php define\n");
    exit(1);
}
$defined = static function (array $defs, string $what) use ($check): void {
    foreach ($defs['functions'] as $f) $check(function_exists($f), "function {$f} is not defined {$what}");
    foreach ($defs['constants'] as $c) $check(defined($c), "constant {$c} is not defined {$what}");
};

require __DIR__ . '/vendor/autoload.php';

// Requiring the autoloader costs what composer.json's autoload.files cost, nothing else:
// a classmap loads classes only, so every function and constant must be there already.
$loaded = array_values(array_map(
    static fn (string $f): string => substr($f, strlen($pkg) + 1),
    array_filter(get_included_files(), static fn (string $f): bool => str_starts_with($f, $pkg . '/'))));
sort($loaded);
$check($loaded === ['php/src/Limits.php', 'php/src/SelError.php', 'php/src/Sql/autoload.php'],
    'requiring vendor/autoload.php loads ' . implode(', ', $loaded)
    . ', not just php/src/Limits.php, php/src/SelError.php and php/src/Sql/autoload.php');
foreach (['Sel\Sel', 'Sel\Registry', 'Sel\Value', 'Sel\Sql\Translator'] as $class) {
    $check(!class_exists($class, false), "{$class} is loaded before anything uses it");
}
$defined($baseline['language'], 'by vendor/autoload.php');

if (!in_array($scenario, ['define', 'lazy'], true)) {
    fwrite(STDERR, "usage: probe.php lazy|define\n");
    exit(2);
}
try {
    if ($scenario === 'define') {
        // An application's define() before anything has read the table: a shipped name is
        // refused there, as after bootstrap.php, and every shipped module still registers.
        try {
            \Sel\Registry::define(['name' => 'UPPER', 'min' => 1, 'max' => 1,
                'fn' => static fn (\Sel\Args $a): \Sel\Value => \Sel\Value::text('application')]);
            $check(false, 'define() of the shipped UPPER before the first evaluation was accepted');
        } catch (\LogicException $e) {
            $check($e->getMessage() === 'SEL function UPPER defined twice',
                'define() of the shipped UPPER: ' . $e->getMessage());
        }
        \Sel\Registry::define(['name' => 'APP_REPEAT', 'min' => 1, 'max' => 1,
            'fn' => static fn (\Sel\Args $a): \Sel\Value => \Sel\Value::text(str_repeat('x', $a->nonNegInt(0)))]);
        $got = \Sel\Sel::evaluate('UPPER("a") & ABS(-1) & APP_REPEAT(3)')->asText();
        $check($got === 'A1xxx', "after define(): UPPER(\"a\") & ABS(-1) & APP_REPEAT(3) gave {$got}");
        $missing = array_diff(array_keys(\Sel\BuiltinManifest::BUILTINS), \Sel\Registry::names());
        $check($missing === [], 'after define(), shipped builtins missing: ' . implode(', ', $missing));
        $check(!\Sel\Registry::mayHaveEffects('UPPER') && \Sel\Registry::mayHaveEffects('APP_REPEAT'),
            'after define(), UPPER is not the shipped one or APP_REPEAT is not the application\'s');
    } elseif ($scenario === 'lazy') {
        $two = \Sel\Sel::evaluate('1 + 1')->asText();
        $check($two === '2', "1 + 1 gave {$two}");
        $check(!class_exists('Sel\Sql\Translator', false), 'evaluating loaded the SQL layer');
        $sql = \Sel\Sql\Sql::translate(\Sel\Sel::compile('X > 1'), 'sqlite',
            ['X' => \Sel\Sql\Binding::column('x', null, 'NUM')])->asCondition();
        $check($sql === '(CAST("x" AS NUMERIC) > CAST(\'1\' AS NUMERIC))', "X > 1 in sqlite gave {$sql}");
        $check(class_exists('Sel\Sql\Translator', false), 'naming Sel\Sql\Sql did not load the SQL layer');
        $defined($baseline['sql'], 'once a Sel\Sql class is named');
        // Every class either bootstrap declares, found by the autoloader, from the installed copy.
        foreach (array_merge($baseline['language']['classes'], $baseline['sql']['classes']) as $class) {
            if (!class_exists($class) && !interface_exists($class) && !trait_exists($class)) {
                $check(false, "the autoloader cannot load {$class}");
                continue;
            }
            $file = (string) (new \ReflectionClass($class))->getFileName();
            $check(str_starts_with($file, $pkg . '/php/src/'), "{$class} came from {$file}, not the installed package");
        }
    }
} catch (\Throwable $e) {
    $check(false, get_class($e) . ': ' . $e->getMessage() . ' (' . basename($e->getFile()) . ':' . $e->getLine() . ')');
}

if ($failures !== []) {
    foreach ($failures as $f) echo "FAIL {$scenario}: {$f}\n";
    exit(1);
}
echo "{$scenario}: ok, " . count($baseline['language']['classes']) . '+' . count($baseline['sql']['classes']) . " classes\n";
PHP

probe() {   # probe <label> <scenario>
  local out
  if ! out="$(cd "$app" && php probe.php "$2" 2>&1)"; then
    fail "$1, $2:"
    printf '%s\n' "$out" | sed 's/^/       /'
  fi
}
probe "default autoloader" lazy
probe "default autoloader" define

# The production setting: only the classmap is searched for the package's classes.
if run_composer dump-autoload -d "$app" --classmap-authoritative > "$WORK/dump.log" 2>&1; then
  probe "--classmap-authoritative" lazy
  probe "--classmap-authoritative" define
else
  fail "composer dump-autoload --classmap-authoritative failed"
  tail -20 "$WORK/dump.log" | sed 's/^/       /'
fi

# 4. The conformance suite through Composer's autoloader: the installed runner with its
#    one require of bootstrap.php replaced by vendor/autoload.php, and the suite's files
#    named (the copy has no conformance/).
runner="$app/conformance"
sed -e "s#^require_once __DIR__ \. '/\.\./src/bootstrap\.php';\$#require_once '$app/vendor/autoload.php';#" \
    -e "s#^require_once __DIR__ \. '/harness\.php';\$#require_once '$inst/php/bin/harness.php';#" \
    "$inst/php/bin/conformance" > "$runner"
if grep -q 'bootstrap\.php' "$runner" || ! grep -q "^require_once '$app/vendor/autoload.php';\$" "$runner" \
   || ! grep -q "^require_once '$inst/php/bin/harness.php';\$" "$runner"; then
  fail "php/bin/conformance no longer requires bootstrap.php and harness.php as this lane rewrites them"
elif ! out="$(php "$runner" "$ROOT"/conformance/*.selt 2>&1)"; then
  fail "conformance through Composer's autoloader:"
  printf '%s\n' "$out" | grep -v '^ok ' | tail -20 | sed 's/^/       /'
else
  conformance="$(printf '%s\n' "$out" | tail -1)"
fi

# 5. The bin composer.json declares.
out="$("$app/vendor/bin/sel" -e '2.50 + 2.50' 2>&1)"
[ "$out" = "5.00" ] || fail "vendor/bin/sel printed '$out' for 2.50 + 2.50"

[ "$status" -eq 0 ] && echo "php package: nathanjel/sel-lang — $(find "$pkg" -type f | wc -l) files; loads lazily (default and authoritative classmap), define() before first use, conformance through Composer (${conformance:-?}), vendor/bin/sel all good"
exit "$status"
