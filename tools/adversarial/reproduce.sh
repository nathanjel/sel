#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
for name in sel-audit-pg-0915 sel-audit-maria-0915 sel-audit-tools-0915; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "Container $name already exists; stop this audit's previous run before reproducing." >&2
    exit 1
  fi
done
created=()
cleanup() {
  if ((${#created[@]})); then docker stop "${created[@]}" >/dev/null; fi
}
trap cleanup EXIT
docker run -d --rm --name sel-audit-pg-0915 -e POSTGRES_PASSWORD=sel_audit -e POSTGRES_DB=sel_audit postgres@sha256:ef92240eff6b0bcce8ccf038c2edcd0d8fd8ef90621849993b8c8995881ab09a
created+=(sel-audit-pg-0915)
docker run -d --rm --name sel-audit-maria-0915 -e MARIADB_ROOT_PASSWORD=sel_audit -e MARIADB_DATABASE=sel_audit mariadb@sha256:efb4959ef2c835cd735dbc388eb9ad6aab0c78dd64febcd51bc17481111890c4
created+=(sel-audit-maria-0915)
docker run -d --rm --name sel-audit-tools-0915 -v "$PWD:/work:ro" -w /work python@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203 sleep infinity
created+=(sel-audit-tools-0915)
docker exec sel-audit-tools-0915 sh -c 'apt-get update -qq && apt-get install -y -qq sbcl cl-ppcre php-cli php-mysql php-pgsql php-sqlite3'
docker exec sel-audit-tools-0915 pip install --quiet --target /tmp/sel-wheel /work
npm install --ignore-scripts --package-lock=false
npm run build
make -C cpp -j4 build/batch build/sqlt
g++ -std=c++23 -O2 tools/adversarial/translate.cpp cpp/build/sel.o \
 cpp/build/sel_sql.o cpp/build/sel_sql_binding.o cpp/build/sel_sql_emit.o \
 cpp/build/sel_sql_map.o cpp/build/sel_sql_map_data.o cpp/build/sel_sql_node.o \
 cpp/build/sel_sql_stage1.o cpp/build/sel_sql_translator.o \
 cpp/build/sel_sql_hybrid.o -o cpp/build/adversarial
python3 tools/adversarial/run.py
python3 tools/adversarial/replay.py
python3 tools/adversarial/scale.py
python3 tools/adversarial/scale-db.py
python3 tools/adversarial/verify.py
