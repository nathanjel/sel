# The PHP client the database oracle runs in: PHP CLI with every PDO driver
# sql/oracle asks for (mysql-family, postgresql, sqlite), on a base whose
# libsqlite3 is new enough. The SQLite driver IS the SQLite server -- there is
# no container for it -- so this image's library decides what `sqlite`
# answers, and 3.46.1 (Debian trixie's) truncates a substr() length past 2^31:
# substr('Zażółć', 2, 4294967299) is 'ażó' there and 'ażółć' from 3.48.0 on.
# tools/oracle-db.sh refuses a client below that floor (SEL-0041).
#
#   docker build -t sel-php-oracle:local -f tools/oracle-php-client.Dockerfile tools
#
# and run the oracle through it with a `php` shim on PATH that mounts the
# checkout and shares the host network, e.g.
#
#   docker run --rm --network host -v "$PWD:$PWD" -w "$PWD" sel-php-oracle:local php "$@"
FROM php:cli-alpine
RUN apk add --no-cache postgresql-dev \
 && docker-php-ext-install pdo_mysql pdo_pgsql
