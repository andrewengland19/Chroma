#!/bin/bash
# =============================================================================
# CHROMA → Force Update (tray / app menu). Started detached by the app, which then
# quits. Steps:
#   1. wait for the app (pid $1) to exit (it stops the engine it spawned)
#   2. `lite show stop` if the hub is running
#   3. end every CHROMA process: engines (hub/chromad.py, any home), app binaries,
#      `tauri dev` / Vite of a Chroma checkout, the old Electron app. SIGTERM first (an
#      engine saves its console + a take being recorded), SIGKILL after 5 s.
#   4. relaunch the latest dev version: `npm run tauri dev` in the main checkout ($2)
#      — whatever local main has; nothing is pulled, no branch is switched.
# Usage: force_update.sh APP_PID CHECKOUT LOG        (FORCE_UPDATE_DRY_RUN=1: plan only;
#        FORCE_UPDATE_ONLY=<regex>: tests — only processes whose command line or cwd matches)
# Keeps CHROMA_HOME / CHROMAD_ARGS / CHROMAD_CMD / CHROMA_SUPERVISE from the app's env.
# =============================================================================
set -u
APP_PID="${1:?app pid}"
CHECKOUT="${2:?checkout}"
LOG="${3:-/dev/stdout}"
DRY="${FORCE_UPDATE_DRY_RUN:-}"
ONLY="${FORCE_UPDATE_ONLY:-}"
SELF=$$

[ "$LOG" != /dev/stdout ] && mkdir -p "$(dirname "$LOG")" && exec >>"$LOG" 2>&1
say() { echo "$(date '+%H:%M:%S') force-update: $*"; }
run() { if [ -n "$DRY" ]; then say "would run: $*"; else "$@"; fi; }

export PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"
if ! command -v npm >/dev/null 2>&1; then
  NODE_BIN=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)
  [ -n "$NODE_BIN" ] && export PATH="$NODE_BIN:$PATH"
fi
unset CHROMA_SELFTEST CHROMA_SELFTEST_HOLD_S   # a relaunched app must not quit itself

say "start (app pid $APP_PID, checkout $CHECKOUT${DRY:+, dry run})"

# 1. the app quits by itself; give it time to stop its engine cleanly
if [ -z "$DRY" ]; then
  for _ in $(seq 1 80); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.1; done
fi

# 2. the old hub
if [ -z "$ONLY" ] && command -v lite >/dev/null 2>&1; then
  if lite show status 2>/dev/null | grep -qi running; then
    say "lite show is running: stopping it"
    run lite show stop
  else
    say "lite show is not running"
  fi
fi

# 3. every CHROMA process (never this script or its parents)
chroma_pids() {
  {
    pgrep -f 'hub/chromad\.py'
    pgrep -x chroma
    pgrep -f 'Chroma\.app/Contents/MacOS/'
    pgrep -f 'Chroma[^ ]*/app/node_modules/\.bin/tauri'
    pgrep -f 'Chroma[^ ]*/app/node_modules/.*vite'
    pgrep -f 'Chroma[^ ]*/chroma/node_modules/.*[Ee]lectron'
    # `npm run tauri dev` / `npm run dev` carry no path: match by working directory
    for p in $(pgrep -f '^npm (run|exec) (tauri|dev)'); do
      lsof -a -p "$p" -d cwd -Fn 2>/dev/null | grep -q '^n.*/Chroma[^/]*/app$' && echo "$p"
    done
  } | sort -un | while read -r p; do
    [ "$p" = "$SELF" ] || [ "$p" = "$PPID" ] && continue
    if [ -n "$ONLY" ]; then                     # tests: command line or working dir must match
      { ps -o command= -p "$p"; lsof -a -p "$p" -d cwd -Fn; } 2>/dev/null | grep -qE "$ONLY" || continue
    fi
    echo "$p"
  done
}

PIDS=$(chroma_pids)
if [ -n "$PIDS" ]; then
  for p in $PIDS; do say "ending $p: $(ps -o command= -p "$p" 2>/dev/null | cut -c1-140)"; done
  if [ -z "$DRY" ]; then
    kill -TERM $PIDS 2>/dev/null
    for _ in $(seq 1 50); do
      LEFT=$(for p in $PIDS; do kill -0 "$p" 2>/dev/null && echo "$p"; done)
      [ -z "$LEFT" ] && break
      sleep 0.1
    done
    if [ -n "${LEFT:-}" ]; then
      say "still running after 5 s, killing: $(echo $LEFT)"
      kill -KILL $LEFT 2>/dev/null
    fi
  fi
else
  say "no other CHROMA processes running"
fi

# 4. the latest dev version
APP_DIR="$CHECKOUT/app"
if [ ! -f "$APP_DIR/package.json" ]; then
  say "no app at $APP_DIR — not relaunching"
  exit 1
fi
say "checkout at $(git -C "$CHECKOUT" log -1 --format='%h %s' 2>/dev/null) ($(git -C "$CHECKOUT" branch --show-current 2>/dev/null))"
cd "$APP_DIR" || exit 1
if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  say "dependencies changed: npm install"
  run npm install --no-audit --no-fund
fi
say "launching npm run tauri dev in $APP_DIR (CHROMA_HOME=${CHROMA_HOME-<default>}, CHROMAD_ARGS=${CHROMAD_ARGS-<default>})"
if [ -z "$DRY" ]; then
  nohup npm run tauri dev </dev/null >>"$LOG" 2>&1 &
  say "launched (pid $!); the window appears when the build finishes"
fi
