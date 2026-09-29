#!/bin/bash
# run-mem.sh <tsv> : name \t program → one line per host
cd /tmp/claude-1001/-home-nathan-workspaces-nth-sel/7de8ec84-1e87-40e4-9fd1-1f6ea31c441b/scratchpad/wt2
while IFS=$'\t' read -r n p; do
  echo "== $n"
  for h in js php py cpp lisp; do
    case $h in
      js) o=$(node js/bin/sel.mjs -e "$p" 2>&1);;
      php) o=$(php php/bin/sel -e "$p" 2>&1);;
      py) o=$(PYTHONPATH=$PWD/python python3 -m sel -e "$p" 2>&1);;
      cpp) o=$(cpp/build/sel -e "$p" 2>&1);;
      lisp) o=$(lisp/bin/sel -e "$p" 2>&1);;
    esac
    printf '  %-5s %s\n' $h "$(echo "$o" | tr '\n' ' ' | cut -c1-150)"
  done
done < "$1"
