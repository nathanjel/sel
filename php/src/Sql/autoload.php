<?php
// The SQL layer for Composer: loaded the first time an application names one of
// its classes (`Sel\Sql\Sql`, `Binding`, `SqlError`, ...), and never otherwise.
//
// composer.json lists this file, not Sql/bootstrap.php, under autoload.files, so
// an application that only evaluates pays for registering one closure -- not for
// parsing the translator and the dialect tables, which is what made requiring
// the package cost twice what the language alone does. Outside Composer the
// layer is opt-in as before: require php/src/Sql/bootstrap.php.

declare(strict_types=1);

spl_autoload_register(static function (string $class): void {
    if (str_starts_with($class, 'Sel\\Sql\\')) {
        require_once __DIR__ . '/bootstrap.php';
    }
});
