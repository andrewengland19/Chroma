#!/usr/bin/env python3
# =============================================================================
# tools/ui_compare.py — render the mock UI at 2x and put it next to a mockup.
#
#   npm --prefix app run dev:mock -- --port 1430        (in another terminal)
#   hub/.venv/bin/python tools/ui_compare.py console out/   [--url ...] [--region x0 y0 x1 y1]
#
# Captures http://localhost:1430/?mock=mockup-<screen> with headless Chrome (a
# throwaway profile; never the user's) at 1178x691 CSS px, device scale 2 — the
# mockups' logical size — then writes <screen>-ui.png, <screen>-mock.png (the
# mockup's window content, same size) and <screen>-side.png (side by side), plus
# optional crops of a region (logical px) for close comparison.
# =============================================================================

import argparse
import os
import subprocess
import sys
import tempfile

from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H, SCALE = 1178, 691, 2
# Window content inside docs/mvp/gui-*.png (2400x1500): left/top/right/bottom in image px.
MOCK_BOX = (21.6, 96, 2378.4, 1478.4)
MOCKS = {"console": "gui-1-console.png", "takes": "gui-2-takes.png", "setup": "gui-3-setup.png"}


def capture(url: str, out: str) -> None:
    """Headless Chrome writes the PNG but may not exit on macOS: wait for the file to
    settle, then end that Chrome (its own process group) — never any other Chrome."""
    import signal
    import time
    if os.path.exists(out):
        os.remove(out)
    with tempfile.TemporaryDirectory(prefix="chroma-shot-") as prof:
        proc = subprocess.Popen([CHROME, "--headless=new", f"--user-data-dir={prof}", "--hide-scrollbars",
                                 "--no-first-run", "--disable-gpu", f"--window-size={W},{H}",
                                 f"--force-device-scale-factor={SCALE}", "--run-all-compositor-stages-before-draw",
                                 "--timeout=3000", f"--screenshot={out}", url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline, last = time.monotonic() + 45, -1
            while time.monotonic() < deadline:
                if proc.poll() is not None and os.path.exists(out):
                    break
                size = os.path.getsize(out) if os.path.exists(out) else -1
                if size > 0 and size == last:
                    break
                last = size
                time.sleep(0.5)
            else:
                raise RuntimeError("headless Chrome produced no screenshot")
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("screen", choices=sorted(MOCKS))
    ap.add_argument("outdir")
    ap.add_argument("--url", default=None)
    ap.add_argument("--tab", default=None, help="tab query param for non-console screens")
    ap.add_argument("--region", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"))
    a = ap.parse_args(argv)
    os.makedirs(a.outdir, exist_ok=True)
    url = a.url or f"http://localhost:1430/?mock=mockup-{a.screen}&freeze&tab={a.screen}"
    ui = os.path.join(a.outdir, f"{a.screen}-ui.png")
    capture(url, ui)
    shot = Image.open(ui).convert("RGB")
    mock = Image.open(os.path.join(REPO, "docs", "mvp", MOCKS[a.screen])).convert("RGB")
    mock = mock.crop(tuple(int(round(v)) for v in MOCK_BOX)).resize(shot.size, Image.LANCZOS)
    mock.save(os.path.join(a.outdir, f"{a.screen}-mock.png"))
    side = Image.new("RGB", (shot.width * 2 + 20, shot.height), (255, 0, 255))
    side.paste(mock, (0, 0))
    side.paste(shot, (shot.width + 20, 0))
    side.save(os.path.join(a.outdir, f"{a.screen}-side.png"))
    if a.region:
        box = tuple(int(v * SCALE) for v in a.region)
        pair = Image.new("RGB", ((box[2] - box[0]), (box[3] - box[1]) * 2 + 10), (255, 0, 255))
        pair.paste(mock.crop(box), (0, 0))
        pair.paste(shot.crop(box), (0, box[3] - box[1] + 10))
        pair.save(os.path.join(a.outdir, f"{a.screen}-region.png"))
    print(f"wrote {a.outdir}/{a.screen}-{{ui,mock,side}}.png" + (" and -region.png" if a.region else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
