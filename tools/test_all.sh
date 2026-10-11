#!/bin/zsh
# Run every test suite: pytest (engine + contracts), UI typecheck + generated-types check + vitest,
# and cargo test for the Tauri shell. Exits non-zero on the first failing suite.
#   tools/test_all.sh            all suites
#   PY=/path/to/python tools/test_all.sh   use another interpreter for pytest
set -euo pipefail
ROOT="${0:A:h:h}"
PY="${PY:-$ROOT/hub/.venv/bin/python}"

echo "== pytest (hub + contracts) =="
(cd "$ROOT/hub" && "$PY" -m pytest -q -W ignore::DeprecationWarning)

echo "== app: generated types up to date =="
npm --prefix "$ROOT/app" run --silent gen:types:check

echo "== app: typecheck =="
npm --prefix "$ROOT/app" run --silent typecheck

echo "== app: vitest =="
npm --prefix "$ROOT/app" run --silent test

echo "== app: cargo test =="
if ! out=$(cd "$ROOT/app/src-tauri" && cargo test --quiet 2>&1); then
  print -r -- "$out"; exit 1
fi
print -r -- "$out" | grep -E "^test result" | head -1

echo "== all suites passed =="
