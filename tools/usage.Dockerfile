# The five hosts, each with a PostgreSQL, a MariaDB and a SQLite driver: what
# the database-backed examples under examples/ need, and nothing else.
#
#   docker build -t sel-usage:local -f tools/usage.Dockerfile tools
#
# tools/check-usage.sh builds it when it is missing and runs every example in
# it, against servers it starts beside it. The repository is bind-mounted, not
# copied: the image carries toolchains and drivers, never SEL itself, so it does
# not go stale when the source changes.
#
# Fedora, because it is what the project is developed on: one compiler, one
# libsqlite3 and one PHP between the examples and every other lane.

FROM fedora:44

RUN dnf -y install --setopt=install_weak_deps=False \
        gcc-c++ make diffutils \
        libpq-devel mariadb-connector-c-devel sqlite-devel \
        php-cli php-pdo php-mysqlnd php-pgsql \
        python3 python3-psycopg3 python3-PyMySQL \
        nodejs24 nodejs24-npm \
        sbcl curl ca-certificates \
    && dnf clean all \
    && ln -sf /usr/bin/node-24 /usr/local/bin/node 2>/dev/null || true

# Node drivers live outside the repository and are found through NODE_PATH, which
# CommonJS resolution honours; examples/lib/db.mjs loads them with createRequire.
RUN mkdir -p /opt/node && cd /opt/node \
    && npm init -y >/dev/null && npm install --no-audit --no-fund pg@8 mariadb@3 >/dev/null
ENV NODE_PATH=/opt/node/node_modules

# Quicklisp with the Lisp drivers, where lisp/bin/boot.lisp looks for it
# ($HOME/quicklisp). HOME is shared and writable, because the examples run as
# the calling user so that nothing they write into the bind mount is root's.
ENV HOME=/opt/home
RUN mkdir -p $HOME && cd $HOME \
    && curl -fsSLO https://beta.quicklisp.org/quicklisp.lisp \
    && sbcl --noinform --non-interactive --load quicklisp.lisp \
         --eval '(quicklisp-quickstart:install)' \
         --eval '(ql:quickload (list :cl-ppcre :fiveam :postmodern :sqlite :cl-mysql))' \
    && rm quicklisp.lisp \
    && chmod -R a+rwX $HOME
