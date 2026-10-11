#!/usr/bin/env python3
# =============================================================================
# tools/position_spike.py — M1 measurement spike: how good is the Apple TV's
# song position? (BUILD_ORDER §5.4, §6 M1). Decides how good takes can be.
#
#   record   connect to the paired Apple TV and log every push update plus a
#            4 Hz poll of pyatv's cached state (whole-second position AND the
#            raw MRP anchor) with Mac wall + monotonic times, as JSONL.
#            Read-only: it never changes playback or touches the bulbs.
#   analyze  turn a recording into docs/mvp/position-spike.md: resolution,
#            anchor availability and age (latency), drift between anchors, and
#            how SongClock behaves on the recording (precise vs coarse-only).
#
#   hub/.venv/bin/python tools/position_spike.py record --duration 360
#   hub/.venv/bin/python tools/position_spike.py analyze ~/.chroma/spike/<file>.jsonl \
#       --md docs/mvp/position-spike.md
#
# During `record`: play for a minute, pause ~15 s, resume, seek forward, seek
# back, skip to the next track, let it play a bit. >= 5 minutes total.
# `--fake` records from FakeProvider instead (used by the tests).
# =============================================================================

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "hub"))


# ---- record --------------------------------------------------------------------

def _sample(kind, playing_obj, anchor, mono, wall):
    from pyatv.const import DeviceState
    p = playing_obj
    return {"kind": kind, "mono": mono, "wall": wall,
            "title": getattr(p, "title", None), "artist": getattr(p, "artist", None),
            "hash": getattr(p, "hash", None) or f"{getattr(p, 'artist', '')}|||{getattr(p, 'title', '')}",
            "state": getattr(getattr(p, "device_state", None), "name", str(getattr(p, "device_state", ""))),
            "playing": getattr(p, "device_state", None) == DeviceState.Playing,
            "position": getattr(p, "position", None), "total": getattr(p, "total_time", None),
            "anchor": anchor}


async def record(out_path: str, duration: float, fake: bool, clock=None) -> int:
    from providers import AppleTVProvider
    clock_mono = (clock.monotonic if clock else time.monotonic)
    clock_wall = (clock.time if clock else time.time)
    loop = asyncio.get_running_loop()
    if fake:
        from testing.fake_provider import FakeProvider, FakeTrack
        script = [(1.0, "pause"), (1.5, "play"), (2.0, "seek", 120_000), (2.5, "next")]
        provider = FakeProvider([FakeTrack("A", "Fake", "X", 180_000), FakeTrack("B", "Fake", "X", 200_000)],
                                clock=clock, script=script)
        get_playing = lambda: provider.playing()            # noqa: E731
    else:
        provider = await AppleTVProvider.connect(loop)
        get_playing = None
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    f = open(out_path, "w")
    n = {"push": 0, "poll": 0}

    def write(rec):
        f.write(json.dumps(rec) + "\n")
        n[rec["kind"]] += 1

    class Listener:
        def playstatus_update(self, _updater, playstatus):
            write(_sample("push", playstatus, provider.raw_anchor(), clock_mono(), clock_wall()))

        def playstatus_error(self, _updater, exc):
            f.write(json.dumps({"kind": "error", "mono": clock_mono(), "error": str(exc)}) + "\n")

    provider.start_push(Listener())
    t0 = clock_mono()
    last_print = 0.0
    try:
        while clock_mono() - t0 < duration:
            if fake:
                provider.tick()
                p = get_playing()
            else:
                p = await provider._atv.metadata.playing()     # cached MRP state, no network round trip
            write(_sample("poll", p, provider.raw_anchor(), clock_mono(), clock_wall()))
            if not fake and clock_mono() - last_print >= 5:
                last_print = clock_mono()
                a = provider.raw_anchor() or {}
                print(f"{clock_mono() - t0:6.0f}s  {getattr(p, 'title', '')!s:30.30}  pos={getattr(p, 'position', None)}"
                      f"  anchor={a.get('elapsed_s')}  pushes={n['push']}", flush=True)
            if clock:
                clock.advance(0.25)
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.25)
    finally:
        provider.stop_push()
        f.close()
        if not fake:
            await provider.close()
    print(f"recorded {n['push']} push + {n['poll']} poll samples → {out_path}")
    return 0


# ---- analyze --------------------------------------------------------------------

def _measured_ms(rec):
    a = rec.get("anchor")
    if not a or a.get("elapsed_s") is None or not a.get("anchor_wall"):
        return None
    rate = (a.get("rate") or 0.0) if rec.get("playing") else 0.0
    return a["elapsed_s"] * 1000.0 + (rec["wall"] - a["anchor_wall"]) * 1000.0 * rate


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def analyze(path: str) -> dict:
    from clock import ClockSample, SongClock
    from testing.clock import FakeClock
    recs = [json.loads(l) for l in open(path) if l.strip()]
    recs = [r for r in recs if r.get("kind") in ("push", "poll")]
    recs.sort(key=lambda r: r["mono"])
    if not recs:
        raise SystemExit("no samples")
    dur_s = recs[-1]["mono"] - recs[0]["mono"]
    pushes = [r for r in recs if r["kind"] == "push"]
    with_anchor = [r for r in recs if r.get("anchor")]
    positions = [r["position"] for r in recs if r.get("position") is not None]
    steps = sorted({b - a for a, b in zip(positions, positions[1:]) if b != a and 0 < b - a <= 5})
    frac = [r["anchor"]["elapsed_s"] % 1 for r in with_anchor if r["anchor"].get("elapsed_s") is not None]
    ages = [(r["wall"] - r["anchor"]["anchor_wall"]) * 1000.0 for r in pushes if r.get("anchor")]

    # Drift: extrapolate the previous anchor to this sample's wall time and compare,
    # within one track while playing at rate 1 and with no seek/pause in between.
    drift, seeks, pauses, tracks = [], 0, 0, 0
    prev = None
    for r in with_anchor:
        m = _measured_ms(r)
        if prev is not None and r["hash"] == prev["hash"] and r["playing"] and prev["playing"]:
            pred = _measured_ms(prev) + (r["wall"] - prev["wall"]) * 1000.0
            err = m - pred
            if abs(err) >= 1500:
                seeks += 1
            elif r["anchor"]["anchor_wall"] != prev["anchor"]["anchor_wall"]:
                drift.append(err)                       # only count genuinely new anchors
        elif prev is not None and r["hash"] != prev["hash"]:
            tracks += 1
        elif prev is not None and r["playing"] != prev["playing"]:
            pauses += 1
        prev = r

    # SongClock replay: precise vs coarse-only, scored against the anchor "truth".
    def replay(use_anchor):
        clk = FakeClock(start_mono=recs[0]["mono"], start_wall=recs[0]["wall"])
        sc = SongClock(clk)
        errs, backsteps, last = [], [], None
        for r in recs:
            dt = r["mono"] - clk.monotonic()
            if dt > 0:
                clk.advance(dt)
            ev = sc.update(ClockSample(r["hash"], r["playing"],
                                       r["position"] * 1000 if r.get("position") is not None else None,
                                       r["total"] * 1000 if r.get("total") else None,
                                       r.get("anchor") if use_anchor else None))
            p = sc.pos_ms()
            truth = _measured_ms(r)
            if truth is not None and p is not None and ev not in ("track", "seek"):
                errs.append(p - truth)
            if last is not None and p is not None and ev not in ("track", "seek") and r["hash"] == last[0]:
                backsteps.append(last[1] - p)
            last = (r["hash"], p)
        return {"err_abs_p50": _pct([abs(e) for e in errs], 0.5), "err_abs_p95": _pct([abs(e) for e in errs], 0.95),
                "err_abs_max": max((abs(e) for e in errs), default=None),
                "max_backstep_ms": max([b for b in backsteps if b > 0], default=0.0)}

    return {
        "file": os.path.basename(path), "duration_s": round(dur_s, 1), "samples": len(recs),
        "push_updates": len(pushes), "anchor_coverage": round(len(with_anchor) / len(recs), 3),
        "coarse_steps_s": steps, "anchor_has_subsecond": any(0.001 < x < 0.999 for x in frac),
        "anchor_age_ms_p50": _pct(ages, 0.5), "anchor_age_ms_p95": _pct(ages, 0.95),
        "drift_ms_n": len(drift),
        "drift_ms_mean": round(statistics.fmean(drift), 1) if drift else None,
        "drift_ms_abs_p95": _pct([abs(d) for d in drift], 0.95),
        "drift_ms_abs_max": max((abs(d) for d in drift), default=None),
        "seeks": seeks, "play_pause_changes": pauses, "track_changes": tracks,
        "songclock_precise": replay(True) if with_anchor else None,
        "songclock_coarse_only": replay(False),
    }


def to_markdown(res: dict) -> str:
    def f(v, unit=""):
        return "n/a" if v is None else (f"{v:.1f}{unit}" if isinstance(v, float) else f"{v}{unit}")
    p, c = res["songclock_precise"] or {}, res["songclock_coarse_only"]
    return f"""# Apple TV position spike — results

Recording `{res['file']}`: {res['duration_s']} s, {res['samples']} samples ({res['push_updates']} push updates),
{res['track_changes']} track changes, {res['play_pause_changes']} play/pause changes, {res['seeks']} seeks.

| Measure | Value |
|---|---|
| pyatv `Playing.position` step | {res['coarse_steps_s']} s (whole seconds, truncated) |
| Samples with the raw MRP anchor | {res['anchor_coverage'] * 100:.1f} % |
| Anchor has sub-second resolution | {'yes' if res['anchor_has_subsecond'] else 'no'} |
| Anchor age when a push arrives (latency + clock offset), p50 / p95 | {f(res['anchor_age_ms_p50'], ' ms')} / {f(res['anchor_age_ms_p95'], ' ms')} |
| Drift between consecutive anchors (n={res['drift_ms_n']}), mean / abs p95 / abs max | {f(res['drift_ms_mean'], ' ms')} / {f(res['drift_ms_abs_p95'], ' ms')} / {f(res['drift_ms_abs_max'], ' ms')} |
| SongClock (precise) error vs anchor, p50 / p95 / max | {f(p.get('err_abs_p50'), ' ms')} / {f(p.get('err_abs_p95'), ' ms')} / {f(p.get('err_abs_max'), ' ms')} |
| SongClock (coarse only) error vs anchor, p50 / p95 / max | {f(c['err_abs_p50'], ' ms')} / {f(c['err_abs_p95'], ' ms')} / {f(c['err_abs_max'], ' ms')} |
| Largest backward step mid-track, precise / coarse | {f(p.get('max_backstep_ms'), ' ms')} / {f(c['max_backstep_ms'], ' ms')} |

## What it means for takes

- If the anchor is available with sub-second resolution and drift stays within a few tens of ms, takes can
  target the ±40 ms replay goal (M5).
- Coarse-only (whole seconds) can only promise the error in the coarse row above.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="position_spike")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--duration", type=float, default=360)
    r.add_argument("--out", default=None)
    r.add_argument("--fake", action="store_true")
    a = sub.add_parser("analyze")
    a.add_argument("path")
    a.add_argument("--md", default=None, help="write the markdown report here")
    args = ap.parse_args(argv)
    if args.cmd == "record":
        from paths import chroma_dir
        out = args.out or os.path.join(chroma_dir(), "spike", time.strftime("position-%Y%m%d-%H%M%S.jsonl"))
        clock = None
        if args.fake:
            from testing.clock import FakeClock
            clock = FakeClock()
        return asyncio.run(record(out, args.duration, args.fake, clock))
    res = analyze(args.path)
    md = to_markdown(res)
    if args.md:
        with open(args.md, "w") as f:
            f.write(md)
        print(f"wrote {args.md}")
    else:
        print(md)
    print(json.dumps(res, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
