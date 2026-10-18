#!/bin/zsh
# Install Chroma.app into /Applications.
#   tools/install_app.sh                 build (npm run bundle) unless a build exists, then install
#   tools/install_app.sh --rebuild       always build first
#   tools/install_app.sh /path/Chroma.app   install that app as-is
# Overwrites only a previous Chroma.app (bundle id com.chroma.console); refuses if Chroma is running.
# Does not touch the Dock or ~/.chroma.
set -euo pipefail
ROOT="${0:A:h:h}"
BUILT="$ROOT/app/src-tauri/target/release/bundle/macos/Chroma.app"
DEST="/Applications/Chroma.app"
SRC=""; REBUILD=0
for a in "$@"; do
  case "$a" in --rebuild) REBUILD=1 ;; *.app) SRC="${a:A}" ;; *) echo "usage: $0 [--rebuild] [Chroma.app]" >&2; exit 2 ;; esac
done
if [[ -z "$SRC" ]]; then
  SRC="$BUILT"
  if (( REBUILD )) || [[ ! -d "$SRC" ]]; then (cd "$ROOT/app" && npm run bundle); fi
fi
[[ -f "$SRC/Contents/Info.plist" ]] || { echo "not an app bundle: $SRC" >&2; exit 1; }
[[ -d "$SRC/Contents/Resources/chromad" ]] || echo "warning: $SRC has no bundled engine (Resources/chromad); it will need CHROMAD_CMD" >&2
ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$SRC/Contents/Info.plist")

if pgrep -f "$DEST/Contents/MacOS/" >/dev/null; then
  echo "Chroma is running: quit it from the tray (Quit) first, then re-run." >&2; exit 1
fi
if [[ -e "$DEST" ]]; then
  OLD=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$DEST/Contents/Info.plist" 2>/dev/null || true)
  [[ "$OLD" == "$ID" ]] || { echo "$DEST exists and is not Chroma (bundle id '${OLD:-unknown}'); not touching it." >&2; exit 1; }
  rm -rf "$DEST"
fi
ditto "$SRC" "$DEST"
echo "installed $DEST ($(du -sh "$DEST" | cut -f1))"
cat <<MSG

First launch of an unsigned app: right-click Chroma in /Applications > Open > Open
(or: xattr -dr com.apple.quarantine /Applications/Chroma.app). Not needed for an app built on this Mac.

Add to the Dock: open /Applications, drag Chroma onto the Dock; or launch it and
right-click its Dock icon > Options > Keep in Dock.
MSG
