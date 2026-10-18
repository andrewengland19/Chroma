#!/bin/zsh
# Build the chromad sidecar: a PyInstaller one-directory bundle of hub/chromad.py, written to
# app/src-tauri/sidecar/chromad/ (git-ignored). `tauri build` ships that folder inside the .app as
# Contents/Resources/chromad/ (tauri.conf.json bundle.resources); the app and the login LaunchAgent
# run Contents/Resources/chromad/chromad. See docs/mvp/DECISIONS.md (2026-10-18) for why PyInstaller.
#   tools/build_sidecar.sh            build (uses hub/.venv; installs PyInstaller into it if missing)
#   tools/build_sidecar.sh --smoke    build, then start the result with --fake and check its socket
set -euo pipefail
ROOT="${0:A:h:h}"
VENV="$ROOT/hub/.venv"
OUT="$ROOT/app/src-tauri/sidecar"
[[ -x "$VENV/bin/python" ]] || { echo "hub/.venv missing: python3 -m venv hub/.venv && hub/.venv/bin/pip install -r hub/requirements.txt" >&2; exit 1; }
"$VENV/bin/python" -c "import PyInstaller" 2>/dev/null || "$VENV/bin/pip" install -q "pyinstaller>=6"

rm -rf "$OUT" "$ROOT/build/pyinstaller"
"$VENV/bin/pyinstaller" --noconfirm --clean --onedir --name chromad \
  --distpath "$OUT" --workpath "$ROOT/build/pyinstaller" --specpath "$ROOT/build/pyinstaller" \
  --paths "$ROOT/hub" \
  --hidden-import lights_agent --hidden-import lights_prompt \
  --collect-submodules pyatv --collect-submodules lifxlan --collect-submodules anthropic \
  --collect-data pyatv --collect-data certifi \
  "$ROOT/hub/chromad.py"
echo "sidecar built: $OUT/chromad/chromad"

if [[ "${1:-}" == "--smoke" ]]; then
  H=$(mktemp -d /tmp/cxs.XXXX)
  "$OUT/chromad/chromad" --fake --home "$H" >"$H/out.log" 2>&1 &
  PID=$!
  for _ in {1..100}; do [[ -S "$H/chromad.sock" ]] && break; sleep 0.2; done
  CHROMA_HOME="$H" python3 "$ROOT/tools/chromactl.py" state | grep -q '"title"' && echo "smoke ok: sidecar answers get_state"
  RC=$?
  kill $PID 2>/dev/null; wait $PID 2>/dev/null || true
  rm -rf "$H"
  exit $RC
fi
