# =============================================================================
# Characterization tests for the AUTO (album-art painting) path.
#
# Golden values were captured from main @ a8d3142 (before any MVP change) with
# the fake bulbs + FakeProvider + GOLDEN_LAYOUT in conftest.py. If one of these
# fails, the art painter's behaviour changed — that is a regression unless the
# user explicitly asked for it.
# =============================================================================

import json
import os

import pytest

from testing.art import OLDER, make_art
from testing.clock import FakeClock
from testing.fake_provider import FakeProvider, FakeTrack

ORDER = ["TV Left", "TV Right", "Couch Left", "Right couch", "Washer"]

DET_GOLDEN = {
    "TV Left": (64321, 44514, 58711, 3500),
    "TV Right": (4468, 47271, 65535, 3500),
    "Couch Left": (50411, 41558, 46375, 3500),
    "Right couch": (46420, 45589, 27871, 3500),
    "Washer": (50436, 41262, 45861, 3500),
}
DET_HEX = {"TV Left": "#d44454", "TV Right": "#f48c44", "Couch Left": "#7c3ca4",
           "Right couch": "#2c1c5c", "Washer": "#7b3ca2"}

PAINT = [{"hex": "#ff0000", "x": 0.2, "y": 0.5}, {"hex": "#0000ff", "x": 0.8, "y": 0.5}]
PAINT_RR_GOLDEN = {
    "TV Left": (0, 65535, 49151, 3500), "TV Right": (43690, 65535, 49151, 3500),
    "Couch Left": (0, 65535, 49151, 3500), "Right couch": (43690, 65535, 49151, 3500),
    "Washer": (0, 65535, 49151, 3500),
}
PAINT_SP_GOLDEN = {
    "TV Left": (64023, 47363, 51412, 3500), "TV Right": (43498, 49367, 53211, 3500),
    "Couch Left": (64023, 47363, 51412, 3500), "Right couch": (43498, 49367, 53211, 3500),
    "Washer": (51570, 31958, 36506, 3500),
}

SNAPSHOT_KEYS = ['ai', 'album', 'artist', 'colors', 'config', 'connected', 'device', 'enabled',
                 'layout', 'lights', 'mode', 'per_light', 'playing', 'scene', 'title', 'track',
                 'updated_at']


def drain(q) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def pushed(bulbs) -> dict:
    return {b.label: [c for c in b.calls if c[0] == "set_color"] for b in bulbs}


async def paint_once(eng, prov, source="push"):
    await eng._on_playing(await prov.now_playing(), source)


# ---- deterministic -----------------------------------------------------------

async def test_deterministic_paint_matches_golden(make_engine):
    import engine as E
    eng, bulbs, prov = await make_engine()
    q = eng.bus.subscribe()
    await paint_once(eng, prov)

    calls = pushed(bulbs)
    for label in ORDER:
        assert len(calls[label]) == 1, label
        _, hsbk, duration, rapid, _t = calls[label][0]
        assert hsbk == DET_GOLDEN[label], label
        assert duration == 1500 and rapid is True
    assert all(not [c for c in b.calls if c[0] == "set_power"] for b in bulbs)

    with open(E.COLORS_FILE) as f:
        payload = json.load(f)
    assert payload["track"] == "Sasha Alex Sloan — Older"
    assert [tuple(c.values()) for c in payload["colors"]] == [DET_GOLDEN[l] for l in ORDER]

    with open(E.ARTWORK_FILE, "rb") as f:
        assert f.read() == prov.track.art

    events = drain(q)
    assert [e["event"] for e in events] == ["track_change", "colors_pushed"]
    cp = events[1]
    assert cp["enabled"] is True and cp["mode"] == "deterministic" and cp["scene_active"] is False
    assert [(p["label"], p["hex"]) for p in cp["per_light"]] == [(l, DET_HEX[l]) for l in ORDER]

    with open(E.STATE_FILE) as f:
        assert sorted(json.load(f).keys()) == SNAPSHOT_KEYS


@pytest.mark.parametrize("dist,golden", [("round_robin", PAINT_RR_GOLDEN),
                                         ("spatial", PAINT_SP_GOLDEN)])
async def test_paint_mode_matches_golden(make_engine, dist, golden):
    eng, bulbs, prov = await make_engine(mode="paint", paint_colors=PAINT,
                                         paint_distribution=dist)
    await paint_once(eng, prov)
    calls = pushed(bulbs)
    assert {l: calls[l][0][1] for l in ORDER} == golden


async def test_disabled_painting_computes_but_does_not_push(make_engine):
    import engine as E
    eng, bulbs, prov = await make_engine(enabled=False)
    q = eng.bus.subscribe()
    await paint_once(eng, prov)
    assert all(not b.calls for b in bulbs)
    with open(E.COLORS_FILE) as f:
        assert [tuple(c.values()) for c in json.load(f)["colors"]] == [DET_GOLDEN[l] for l in ORDER]
    cp = [e for e in drain(q) if e["event"] == "colors_pushed"][0]
    assert cp["enabled"] is False


async def test_no_artwork_leaves_lights_alone(make_engine):
    prov = FakeProvider([FakeTrack("Bare", "Nobody", "None", 120_000, art=None)], clock=FakeClock())
    eng, bulbs, _ = await make_engine(provider=prov)
    q = eng.bus.subscribe()
    await paint_once(eng, prov)
    assert all(not b.calls for b in bulbs)
    assert {"event": "status", "message": "no artwork for this track"} in drain(q)


async def test_push_failure_on_one_bulb_is_swallowed(make_engine):
    eng, bulbs, prov = await make_engine()
    bulbs[0].fail = True
    await paint_once(eng, prov)
    calls = pushed(bulbs)
    assert calls["TV Left"] == []
    assert {l: calls[l][0][1] for l in ORDER[1:]} == {l: DET_GOLDEN[l] for l in ORDER[1:]}


# ---- dedup, resume, track change ---------------------------------------------

async def test_same_track_does_not_repaint_but_resume_does(make_engine):
    eng, bulbs, prov = await make_engine()
    await paint_once(eng, prov)
    await paint_once(eng, prov)                       # same track, still playing
    assert all(len(pushed(bulbs)[l]) == 1 for l in ORDER)

    prov.pause()
    await paint_once(eng, prov)                       # → paused: status only
    assert all(len(pushed(bulbs)[l]) == 1 for l in ORDER)

    prov.play()
    await paint_once(eng, prov)                       # paused → playing: repaint
    calls = pushed(bulbs)
    assert all(len(calls[l]) == 2 for l in ORDER)
    assert {l: calls[l][1][1] for l in ORDER} == DET_GOLDEN


async def test_track_change_repaints_with_new_art(make_engine):
    eng, bulbs, prov = await make_engine()
    await paint_once(eng, prov)
    prov.skip(1)
    await paint_once(eng, prov)
    calls = pushed(bulbs)
    assert all(len(calls[l]) == 2 for l in ORDER)
    assert {l: calls[l][1][1] for l in ORDER} != DET_GOLDEN


# ---- AI mode + per-album scenes -----------------------------------------------

class StubEnhancer:
    active = True

    def __init__(self, rgb):
        self.rgb = rgb
        self.calls = 0
        self.last_ok = True
        self.last_mood = "warm"
        self.last_palette = ["#ff8800"]
        self.last_reasoning = "stub"
        self.last_error = ""

    async def enhance(self, artwork, meta, coords, base_rgb):
        self.calls += 1
        return [self.rgb[i % len(self.rgb)] for i in range(len(coords))]


async def test_ai_mode_uses_enhancer_saves_scene_and_recalls_it(make_engine, monkeypatch):
    import engine as E
    from color_pipeline import compute_brightness_scales, rgb_to_hsbk
    from config import Config
    from scenes import SceneStore

    stub = StubEnhancer([(255, 136, 0), (20, 40, 200)])
    monkeypatch.setattr(E, "build_enhancer", lambda cfg: stub)
    eng, bulbs, prov = await make_engine(mode="ai")
    q = eng.bus.subscribe()
    await paint_once(eng, prov)

    rgb = [stub.rgb[i % 2] for i in range(5)]
    params = Config.load().pipeline_params()
    expected = [rgb_to_hsbk(*c, s, params) for c, s in zip(rgb, compute_brightness_scales(rgb, params))]
    calls = pushed(bulbs)
    assert [calls[l][0][1] for l in ORDER] == expected
    assert stub.calls == 1

    scene = SceneStore.load().get("Sasha Alex Sloan|||Loser - EP")
    assert scene["type"] == "ai" and scene["rgb"] == [list(c) for c in rgb]
    reasoning = [e for e in drain(q) if e["event"] == "ai_reasoning"]
    assert reasoning and reasoning[0]["cached"] is False

    # Same album again (new engine, fresh process): scene recalled, model not re-run.
    eng2, bulbs2, prov2 = await make_engine(mode="ai")
    q2 = eng2.bus.subscribe()
    await paint_once(eng2, prov2)
    assert stub.calls == 1
    assert [pushed(bulbs2)[l][0][1] for l in ORDER] == expected
    evs = drain(q2)
    assert any(e["event"] == "ai_reasoning" and e["cached"] for e in evs)
    assert [e for e in evs if e["event"] == "colors_pushed"][0]["scene_active"] is True


# ---- control-plane commands ----------------------------------------------------

async def test_set_mode_repaints_from_cache(make_engine):
    eng, bulbs, prov = await make_engine()
    await paint_once(eng, prov)
    eng.cfg.paint_colors = PAINT
    snap = await eng.set_mode("paint")
    assert snap["mode"] == "paint"
    assert {l: pushed(bulbs)[l][-1][1] for l in ORDER} == PAINT_RR_GOLDEN
    assert (await eng.set_mode("bogus"))["mode"] == "paint"


async def test_set_enabled_toggles_and_repaints(make_engine):
    eng, bulbs, prov = await make_engine(enabled=False)
    await paint_once(eng, prov)
    assert all(not b.calls for b in bulbs)
    snap = await eng.set_enabled(True)
    assert snap["enabled"] is True and eng.cfg.enabled is True
    assert {l: pushed(bulbs)[l][-1][1] for l in ORDER} == DET_GOLDEN
    snap = await eng.set_enabled(False)
    assert snap["enabled"] is False
    from config import Config
    assert Config.load().enabled is False


async def test_snapshot_shape(make_engine):
    eng, bulbs, prov = await make_engine()
    await paint_once(eng, prov)
    snap = eng.snapshot()
    assert sorted(snap.keys()) == SNAPSHOT_KEYS
    assert snap["lights"] == 5 and snap["track"] == "Sasha Alex Sloan — Older"
    assert snap["playing"] is True and snap["mode"] == "deterministic"
    assert sorted(snap["ai"].keys()) == ['auto', 'backend', 'claude_available', 'enabled',
                                         'error', 'last_ok', 'model', 'mood', 'offline',
                                         'ollama_reachable', 'palette', 'reasoning', 'url']
    assert sorted(snap["scene"].keys()) == ['active', 'saved', 'type']


# ---- refresh() (what `lite show refresh` does) ---------------------------------

async def test_refresh_without_provider_reconnects(make_engine):
    eng, _, _ = await make_engine()
    eng.provider = None
    res = await eng.refresh()
    assert res == {"ok": True, "reconnecting": True,
                   "message": "Apple TV not connected — reconnecting now"}
    assert eng._wake.is_set()


async def test_refresh_with_stale_link_reconnects(make_engine):
    eng, _, prov = await make_engine()
    await prov.close()                                   # now_playing() → None
    res = await eng.refresh()
    assert res == {"ok": True, "reconnecting": True,
                   "message": "Apple TV connection was stale — reconnecting"}
    assert eng._disconnected.is_set()


async def test_refresh_nothing_playing(make_engine):
    prov = FakeProvider([], clock=FakeClock())
    eng, _, _ = await make_engine(provider=prov)
    res = await eng.refresh()
    assert res == {"ok": False, "message": "nothing playing on the Apple TV"}


async def test_refresh_repaints_even_for_same_track(make_engine):
    eng, bulbs, prov = await make_engine()
    await paint_once(eng, prov)
    res = await eng.refresh()
    assert res["ok"] is True and res["painted"] is True and res["enabled"] is True
    assert res["message"] == "lights refreshed"
    assert res["track"] == "Sasha Alex Sloan — Older" and res["album"] == "Loser - EP"
    assert res["lights"] == 5 and sorted(res["state"].keys()) == SNAPSHOT_KEYS
    assert all(len(pushed(bulbs)[l]) == 2 for l in ORDER)


async def test_refresh_with_painting_disabled(make_engine):
    eng, bulbs, prov = await make_engine(enabled=False)
    res = await eng.refresh()
    assert res["ok"] is True and res["painted"] is False
    assert res["message"] == "refreshed, but painting is disabled"
    assert all(not b.calls for b in bulbs)


async def test_refresh_with_no_lights_rediscovers(make_engine):
    eng, bulbs, prov = await make_engine(bulbs=[])
    res = await eng.refresh()        # discovery runs (sandbox LifxLAN finds nothing)
    assert res["ok"] is False and res["message"] == "no lights found"
    assert res["lights"] == 0


async def test_refresh_no_artwork(make_engine):
    prov = FakeProvider([FakeTrack("Bare", "Nobody", "None", 120_000, art=None)], clock=FakeClock())
    eng, bulbs, _ = await make_engine(provider=prov)
    res = await eng.refresh()
    assert res["ok"] is False and res["message"] == "no artwork for this track"
    assert all(not b.calls for b in bulbs)
