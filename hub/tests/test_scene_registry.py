# =============================================================================
# Scene Agent M2 (lighting-scene-agent-spec §5, §7.3, §15): registry over
# ~/.spectrum_scenes.json, normalization + descriptors, Driver A recall, capture,
# Taste Profile. Fakes only: no bulbs, no `lite`, no network, no model calls.
# =============================================================================

import functools
import json
import os
import stat
import subprocess
from datetime import datetime, timezone

import pytest

import scene_normalize as norm
import scene_render
import scene_taste
from room_model import load_room
from scene_capture import CaptureError, capture_many, capture_scene
from scene_recall import find_lite, recall
from scene_registry import SceneRegistry, SnapshotStore, load_ips
from testing.fake_room import FakeRoomDevices, fake_lite

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "scene")
NOW = datetime(2026, 10, 18, 20, 0, tzinfo=timezone.utc)
IDS = ["tv-left", "tv-right", "couch-left", "couch-right", "window-lamp", "pendant"]
IPS = {"tv-left": "10.0.0.1", "tv-right": "10.0.0.2", "couch-left": "10.0.0.3",
       "couch-right": "10.0.0.4", "window-lamp": "10.0.0.5", "pendant": "10.0.0.6"}
LABELS = {"tv-left": "TV Left", "tv-right": "TV Right", "couch-left": "Couch Left",
          "couch-right": "Couch Right", "window-lamp": "Window Lamp", "pendant": "Pendant"}
H = lambda deg: int(deg / 360 * 65535)      # noqa: E731


def entry(fid, h=0, s=0, b=0.5, k=2700, by_ip=True):
    e = {"label": LABELS[fid], "h": h, "s": s, "b": int(b * 65535), "k": k}
    if by_ip:
        e["ip"] = IPS[fid]
    return e


SCENES = {
    # purple TV side, warm white couch, window lamp half
    "vibes1": {"saved_at": 100, "lights": [entry("tv-left", H(280), 45000, 0.5, 3500), entry("tv-right", H(270), 40000, 0.5),
                                           entry("couch-left", 0, 0, 0.6, 2400), entry("couch-right", 0, 0, 0.55, 2400),
                                           entry("window-lamp", 0, 0, 0.4, 2700)]},
    "movie": {"saved_at": 200, "lights": [entry("couch-left", 0, 0, 0.2, 2200), entry("couch-right", 0, 0, 0.2, 2200)]},
    "agent.rainy": {"saved_at": 300, "lights": [entry("pendant", 0, 0, 0.9, 6000)]},
    "ha-only": {"saved_at": 50, "lights": [{"label": "Hue strip", "entity": "light.x"}]},
    "bad name; rm": {"lights": []},
}


@pytest.fixture(scope="module")
def room():
    return load_room(os.path.join(DATA, "room.yaml"), os.path.join(DATA, "fixtures.json"))


@pytest.fixture
def reg(tmp_path):
    p = tmp_path / "scenes.json"
    p.write_text(json.dumps({"scenes": SCENES}))
    return SceneRegistry(str(p), SnapshotStore(str(tmp_path / "snaps")), str(tmp_path / "scenes.yaml"))


@pytest.fixture
def lite_exe(tmp_path):
    p = tmp_path / "lite"
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


# ---- registry ----------------------------------------------------------------------
def test_entries_split_lite_and_agent_and_skip_unusable_names(reg):
    es = reg.entries()
    assert [(e.name, e.kind) for e in es] == [("ha-only", "lite"), ("movie", "lite"), ("vibes1", "lite"),
                                              ("agent.rainy", "agent")]
    assert {e.name: e.lights for e in es} == {"ha-only": 0, "movie": 2, "vibes1": 5, "agent.rainy": 1}
    assert any("bad name; rm" in p for p in reg.problems)


@pytest.mark.parametrize("name,ok", [("vibes1", True), ("agent.rainy", True), ("nope", False), ("", False),
                                     ("bad name; rm", False), ("../x", False), (None, False), (7, False)])
def test_has(reg, name, ok):
    assert reg.has(name) is ok


def test_user_list_adds_names_and_notes(reg, tmp_path):
    (tmp_path / "scenes.yaml").write_text(json.dumps({"scenes": {"vibes1": "warm, low", "listed-only": None}}))
    by = {e.name: e for e in reg.entries()}
    assert by["vibes1"].note == "warm, low" and by["vibes1"].in_file
    assert by["listed-only"].in_file is False and reg.has("listed-only")
    (tmp_path / "scenes.yaml").write_text(json.dumps(["a", "b"]))
    assert {"a", "b"} <= set(reg.names())


@pytest.mark.parametrize("content", ["{not json", "[]", '{"scenes": 3}'])
def test_unreadable_scenes_file_is_empty_not_a_crash(tmp_path, content):
    p = tmp_path / "s.json"
    p.write_text(content)
    assert SceneRegistry(str(p), SnapshotStore(str(tmp_path / "x"))).names() == []


def test_missing_scenes_file(tmp_path):
    assert SceneRegistry(str(tmp_path / "none.json"), SnapshotStore(str(tmp_path / "x"))).entries() == []


def test_registry_never_writes_the_scenes_file(reg):
    before = open(reg.scenes_path).read(), os.stat(reg.scenes_path).st_mtime_ns
    reg.entries()
    reg.raw_lights("vibes1")
    assert (open(reg.scenes_path).read(), os.stat(reg.scenes_path).st_mtime_ns) == before


def test_load_ips(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"fixtures": [{"id": "a", "ip": "1.1.1.1"}, {"id": "b"}]}))
    assert load_ips(str(p)) == {"a": "1.1.1.1"}
    assert load_ips(str(tmp_path / "missing")) == {}


# ---- normalization -----------------------------------------------------------------
@pytest.mark.parametrize("raw,power,want", [
    ((0, 0, 65535, 2700), True, {"hue": 0.0, "sat": 0.0, "brightness": 1.0, "kelvin": 2700}),
    ((H(120), 65535, 32768, 3500), True, {"hue": 120.0, "sat": 1.0, "brightness": 0.5, "kelvin": 3500}),
    ((H(120), 2999, 65535, 4000), True, {"hue": 0.0, "sat": 0.0, "brightness": 1.0, "kelvin": 4000}),    # white cut
    ((H(120), 3000, 65535, 4000), True, {"hue": 120.0, "sat": 0.0458, "brightness": 1.0, "kelvin": 4000}),
    ((0, 0, 65535, 2700), False, {"hue": 0.0, "sat": 0.0, "brightness": 0.0, "kelvin": 2700}),           # powered off
    ((0, 0, 100, 500), True, {"hue": 0.0, "sat": 0.0, "brightness": 0.0015, "kelvin": 1500}),            # kelvin clamped
])
def test_hsbk_state(raw, power, want):
    got = norm.hsbk_state(*raw, power=power)
    assert {k: got[k] for k in want} == pytest.approx(want, abs=0.01)


def test_normalize_lights_matches_by_ip_then_label_and_turns_the_rest_off(room):
    unknown_ip = {**entry("couch-left", b=0.6), "ip": "10.0.0.99"}           # ip not in fixtures.json: label decides
    lights = [entry("tv-left", H(280), 45000, 0.5), unknown_ip,
              {"label": "Hue strip", "entity": "light.x"}, {"label": "Garage", "ip": "10.9.9.9", "h": 0, "s": 0, "b": 1, "k": 2700}]
    t, unmatched = norm.normalize_lights(room, lights, IPS)
    assert list(t) == sorted(IDS)
    assert t["tv-left"]["brightness"] == pytest.approx(0.5, abs=0.001) and t["tv-left"]["sat"] > 0.6
    assert t["couch-left"]["brightness"] == pytest.approx(0.6, abs=0.001)         # matched by label
    assert t["pendant"] == {"hue": 0.0, "sat": 0.0, "brightness": 0.0, "kelvin": 2700}
    assert unmatched == ["Garage", "Hue strip"]


def test_ip_wins_over_a_clashing_label(room):
    e = entry("tv-left", b=0.9)
    e["label"] = "Couch Left"
    t, _ = norm.normalize_lights(room, [e], IPS)
    assert t["tv-left"]["brightness"] > 0.8 and t["couch-left"]["brightness"] == 0


def _table(room, **by_id):
    t = {f: norm.off_state(2700) for f in room.fixtures}
    for f, (h, s, b, k) in by_id.items():
        t[f.replace("_", "-")] = {"hue": h, "sat": s, "brightness": b, "kelvin": k}
    return t


@pytest.mark.parametrize("name,lights,want", [
    ("dark", {}, {"lit_count": 0, "mean_brightness": 0.0, "dominant_hue": None, "kelvin_mean": None,
                  "lr_balance": 0.0, "fb_balance": 0.0, "zone_contrast": 0.0}),
    ("one warm white", {"couch_left": (0, 0, 0.4, 2400)},
     {"lit_count": 1, "mean_brightness": 0.4, "peak_brightness": 0.4, "dominant_hue": "white",
      "kelvin_mean": 2400.0, "kelvin_min": 2400, "kelvin_max": 2400}),
    ("blue beats weak red by brightness", {"tv_left": (230, 0.8, 0.6, 2700), "tv_right": (5, 0.8, 0.2, 2700)},
     {"dominant_hue": "blue", "kelvin_mean": None, "lit_count": 2}),
    ("red wraps around 0", {"tv_left": (355, 0.8, 0.5, 2700)}, {"dominant_hue": "red"}),
    ("only left lit -> lr negative (viewer faces the TV at y=0, x=0 is left)",
     {"tv_left": (0, 0, 0.5, 2700), "couch_left": (0, 0, 0.5, 2700)}, {"lr_balance": -1 / 2.25}),    # 1 m left of the seat, reach 2.25 m
    ("only right lit -> lr positive", {"tv_right": (0, 0, 0.5, 2700)}, {"lr_balance": 1 / 2.25}),
    ("only TV side lit -> fb positive (toward the focal wall)", {"tv_left": (0, 0, 0.5, 2700), "tv_right": (0, 0, 0.5, 2700)},
     {"fb_balance": 1.0}),
])
def test_describe(room, name, lights, want):
    d = norm.describe(room, _table(room, **lights))
    for k, v in want.items():
        assert d[k] == pytest.approx(v, abs=1e-3), (name, k)


def test_zone_contrast_across_zones_and_fixture_fallback(room):
    # tv_side (tv-left/right) at 0.8, couch (and seating) dark
    d = norm.describe(room, _table(room, tv_left=(0, 0, 0.8, 2700), tv_right=(0, 0, 0.8, 2700)))
    assert d["zone_contrast"] == pytest.approx(0.8)
    # a room with no zones falls back to the brightest-dimmest fixture spread
    import copy
    flat = copy.copy(room)
    flat.zones = {}
    assert norm.describe(flat, _table(room, pendant=(0, 0, 0.5, 2700)))["zone_contrast"] == pytest.approx(0.5)


def test_light_behind_the_viewer_is_negative_fb(room):
    import copy
    import dataclasses
    r = copy.copy(room)
    r.fixtures = dict(room.fixtures)
    r.fixtures["couch-left"] = dataclasses.replace(room.fixtures["couch-left"], position=(1.5, 4.0, 0.9))
    d = norm.describe(r, _table(room, couch_left=(0, 0, 0.5, 2700)))
    assert d["fb_balance"] == pytest.approx(-0.6 / 2.98, abs=0.001)


def test_descriptors_without_viewpoint_use_canvas_axes(room):
    import copy
    r = copy.copy(room)
    r.viewpoint = None
    d = norm.describe(r, _table(room, tv_right=(0, 0, 0.5, 2700)))
    assert d["lr_balance"] > 0 and d["fb_balance"] > 0       # right of centre, toward y = 0


@pytest.mark.parametrize("hue,fam", [(0, "red"), (14, "red"), (15, "orange"), (50, "yellow"), (120, "green"), (180, "cyan"),
                                     (230, "blue"), (280, "purple"), (320, "magenta"), (350, "red"), (359.9, "red")])
def test_hue_family(hue, fam):
    assert norm.hue_family(hue) == fam


@pytest.mark.parametrize("a,b,close", [
    ({"hue": 0, "sat": 0, "brightness": 0.5, "kelvin": 2700}, {"hue": 0, "sat": 0, "brightness": 0.51, "kelvin": 2720}, True),
    ({"hue": 0, "sat": 0, "brightness": 0.5, "kelvin": 2700}, {"hue": 0, "sat": 0, "brightness": 0.6, "kelvin": 2700}, False),
    ({"hue": 0, "sat": 0, "brightness": 0.5, "kelvin": 2700}, {"hue": 0, "sat": 0, "brightness": 0.5, "kelvin": 3500}, False),
    ({"hue": 350, "sat": 0.8, "brightness": 0.5, "kelvin": 2700}, {"hue": 3, "sat": 0.8, "brightness": 0.5, "kelvin": 4000}, False),   # 13 deg
    ({"hue": 358, "sat": 0.8, "brightness": 0.5, "kelvin": 2700}, {"hue": 2, "sat": 0.8, "brightness": 0.5, "kelvin": 4000}, True),    # wraps
    ({"hue": 10, "sat": 0.8, "brightness": 0.0, "kelvin": 2700}, {"hue": 200, "sat": 0.1, "brightness": 0.0, "kelvin": 6000}, True),   # both dark
])
def test_states_close(a, b, close):
    assert norm.states_close(a, b) is close


# ---- derived snapshots + render integration ------------------------------------------
def test_derive_snapshot_validates_and_feeds_the_renderer(reg, room):
    snap = reg.derive_snapshot(room, "vibes1", IPS, now=NOW)
    assert snap["source"] == "stored" and snap["static"] and snap["captured_at"] == "2026-10-18T20:00:00Z"
    assert snap["fixtures"]["pendant"]["brightness"] == 0
    assert snap["descriptors"]["dominant_hue"] == "purple" and snap["descriptors"]["lit_count"] == 5
    assert reg.derive_snapshot(room, "nope", IPS) is None
    spec = {"name": "agent.like", "base": {"scene": "vibes1", "mode": "start_from"},
            "layers": [{"op": "scale", "target": {"zone": "tv_side"}, "brightness_factor": 0.5}]}
    out = scene_render.render(room, spec, reg.render_snapshots(room, IPS))
    assert out["fixtures"]["tv-left"]["brightness"] == pytest.approx(0.25, abs=0.01)
    assert out["fixtures"]["couch-left"]["brightness"] == pytest.approx(0.6, abs=0.01)


def test_snapshot_example_matches_schema(reg, room):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    c = os.path.join(os.path.dirname(os.path.dirname(HERE)), "contracts")
    r = Registry()
    for f in ("snapshot", "common"):
        r = r.with_resource(f + ".schema.json", Resource.from_contents(json.load(open(os.path.join(c, f + ".schema.json")))))
    v = Draft202012Validator(json.load(open(os.path.join(c, "snapshot.schema.json"))), registry=r)
    for n in ("vibes1", "movie", "ha-only", "agent.rainy"):
        assert not list(v.iter_errors(reg.derive_snapshot(room, n, IPS))), n


def test_captured_snapshot_beats_derived_and_animated_is_not_a_render_base(reg, room):
    reg.snapshots.save({**reg.derive_snapshot(room, "movie", IPS), "source": "capture", "static": False})
    reg.snapshots.save({**reg.derive_snapshot(room, "vibes1", IPS), "source": "capture"})
    assert reg.snapshot(room, "vibes1", IPS)["source"] == "capture"
    rs = reg.render_snapshots(room, IPS)
    assert "vibes1" in rs and "movie" not in rs         # movie was captured as animated
    assert reg.get("movie") is not None                 # ...but is still recallable by name


def test_snapshot_store_refuses_bad_names_and_survives_corruption(tmp_path):
    st = SnapshotStore(str(tmp_path))
    assert st.load("../evil") is None and st.load("missing") is None
    with pytest.raises(ValueError):
        st.save({"scene": "../evil", "fixtures": {}})
    (tmp_path / "bad.json").write_text("{nope")
    assert st.load("bad") is None


# ---- Driver A: recall --------------------------------------------------------------
def test_recall_runs_lite_as_an_argument_vector(reg, lite_exe):
    run = fake_lite(FakeRoomDevices(IDS), {"vibes1": {}})
    res = recall("vibes1", reg, run=run, lite=lite_exe)
    assert res.ok and res.status == "applied" and res.argv == [lite_exe, "scene", "vibes1"]
    assert run.calls == [[lite_exe, "scene", "vibes1"]]


@pytest.mark.parametrize("name", ["nope", "vibes1; reboot", "$(id)", "", "../vibes1", "bad name; rm", None, 12])
def test_recall_refuses_unknown_or_hostile_names_without_starting_a_process(reg, lite_exe, name):
    run = fake_lite(FakeRoomDevices(IDS), {})
    res = recall(name, reg, run=run, lite=lite_exe)
    assert not res.ok and res.status == "refused" and run.calls == []


def test_recall_reports_a_missing_lite_executable(reg, tmp_path):
    run = fake_lite(FakeRoomDevices(IDS), {})
    res = recall("vibes1", reg, run=run, which=lambda _n: None, lite=str(tmp_path / "gone"))
    assert res.status == "unavailable" and "gone" in res.detail and run.calls == []
    res = recall("vibes1", reg, run=run, which=lambda _n: None)
    assert res.status == "unavailable" and "PATH" in res.detail


def test_find_lite_prefers_explicit_then_path(lite_exe, tmp_path):
    assert find_lite(lite_exe) == lite_exe
    assert find_lite(None, which=lambda _n: lite_exe) == lite_exe
    assert find_lite(str(tmp_path)) is None         # a directory is not an executable file


@pytest.mark.parametrize("rc,stderr,detail", [(2, "no such scene\nboom", "boom"), (1, "", "lite exited 1")])
def test_recall_nonzero_exit_is_a_failure(reg, lite_exe, rc, stderr, detail):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, rc, "", stderr)
    res = recall("vibes1", reg, run=run, lite=lite_exe)
    assert not res.ok and res.status == "failed" and res.detail == detail


@pytest.mark.parametrize("exc,word", [(subprocess.TimeoutExpired("lite", 1), "timed out"), (OSError("denied"), "denied")])
def test_recall_process_errors(reg, lite_exe, exc, word):
    def run(argv, **kw):
        raise exc
    res = recall("vibes1", reg, run=run, lite=lite_exe, timeout=1)
    assert not res.ok and res.status == "failed" and word in res.detail


def _scene_hsbk(name):
    """SCENES entry as fake-lite hsbk by fixture id."""
    ids = {v: k for k, v in IPS.items()}
    return {ids[e["ip"]]: (e["h"], e["s"], e["b"], e["k"]) for e in SCENES[name]["lights"]}


def test_recall_verify_reads_back_and_reports_match_and_mismatch(reg, room, lite_exe):
    dev = FakeRoomDevices(IDS)
    run = fake_lite(dev, {"vibes1": _scene_hsbk("vibes1")})
    sleeps = []
    res = recall("vibes1", reg, run=run, lite=lite_exe, reader=dev, room=room, ips=IPS, sleep=sleeps.append, settle_s=0.5)
    assert res.ok and res.verified == "verified" and sleeps == [0.5]
    # a bulb that did not follow (something else, e.g. Chroma, changed it)
    def stale(argv, **kw):
        run(argv, **kw)
        dev.state["tv-left"]["hsbk"] = (0, 0, 65535, 6500)
        return subprocess.CompletedProcess(argv, 0, "", "")
    res = recall("vibes1", reg, run=stale, lite=lite_exe, reader=dev, room=room, ips=IPS, sleep=lambda s: None)
    assert not res.ok and res.verified == "mismatch" and res.mismatches == ["tv-left"] and res.status == "applied"


def test_recall_verify_skipped_without_a_snapshot_and_survives_a_dead_reader(reg, room, lite_exe, tmp_path):
    dev = FakeRoomDevices(IDS)
    run = fake_lite(dev, {"ha-only": {}})
    (tmp_path / "scenes.yaml").write_text(json.dumps(["listed-only"]))
    assert recall("listed-only", reg, run=lambda a, **k: subprocess.CompletedProcess(a, 0, "", ""), lite=lite_exe,
                  reader=dev, room=room, sleep=lambda s: None).verified == "skipped"
    class Dead:
        def read(self):
            raise RuntimeError("no reply")
    res = recall("vibes1", reg, run=run, lite=lite_exe, reader=Dead(), room=room, ips=IPS, sleep=lambda s: None)
    assert res.verified == "skipped" and "read-back failed" in res.detail


# ---- capture -----------------------------------------------------------------------
def _capture_env(reg, lite_exe, animated=()):
    dev = FakeRoomDevices(IDS, hsbk=(0, 0, 10000, 3000))
    scenes = {n: _scene_hsbk(n) for n in ("vibes1", "movie")}
    run = fake_lite(dev, scenes, animated=animated)
    return dev, run, functools.partial(recall, registry=reg, run=run, lite=lite_exe)


def test_capture_stores_a_snapshot_and_restores_the_lights(reg, room, lite_exe):
    dev, run, rec = _capture_env(reg, lite_exe)
    original = dev.read()
    snap = capture_scene("vibes1", registry=reg, room=room, devices=dev, recall_fn=rec, confirmed=True,
                         sleep=lambda s: None, now=NOW)
    assert snap["source"] == "capture" and snap["static"] is True
    assert snap["fixtures"]["tv-left"]["sat"] > 0.6 and snap["fixtures"]["pendant"]["brightness"] == 0
    assert reg.snapshots.load("vibes1") == snap
    assert dev.restored == [original] and dev.state == original            # the room is back as it was
    assert run.calls == [[lite_exe, "scene", "vibes1"]]


def test_capture_marks_a_moving_scene_non_static(reg, room, lite_exe):
    dev, _run, rec = _capture_env(reg, lite_exe, animated=["movie"])
    snap = capture_scene("movie", registry=reg, room=room, devices=dev, recall_fn=rec, confirmed=True, sleep=lambda s: None)
    assert snap["static"] is False
    assert "movie" not in reg.render_snapshots(room, IPS)


def test_capture_waits_for_the_lights_to_settle(reg, room, lite_exe):
    dev, _run, rec = _capture_env(reg, lite_exe)
    sleeps = []
    capture_scene("vibes1", registry=reg, room=room, devices=dev, recall_fn=rec, confirmed=True,
                  settle_s=2.5, recheck_s=0.75, sleep=sleeps.append)
    assert sleeps == [2.5, 0.75]


def test_capture_requires_confirmation_and_a_registered_name(reg, room, lite_exe):
    dev, run, rec = _capture_env(reg, lite_exe)
    for kw, word in (({"confirmed": False}, "confirmed"), ({"name": "nope", "confirmed": True}, "registry")):
        with pytest.raises(CaptureError, match=word):
            capture_scene(**{"name": "vibes1", "registry": reg, "room": room, "devices": dev, "recall_fn": rec,
                             "sleep": lambda s: None, **kw})
    assert run.calls == [] and dev.reads == 0 and dev.restored == []


def test_capture_failure_still_restores_and_stores_nothing(reg, room, lite_exe):
    dev = FakeRoomDevices(IDS)
    run = fake_lite(dev, {}, returncode=1)
    rec = functools.partial(recall, registry=reg, run=run, lite=lite_exe)
    before = dev.read()
    with pytest.raises(CaptureError, match="could not recall"):
        capture_scene("vibes1", registry=reg, room=room, devices=dev, recall_fn=rec, confirmed=True, sleep=lambda s: None)
    assert dev.restored == [before] and reg.snapshots.load("vibes1") is None


def test_capture_reports_a_restore_failure(reg, room, lite_exe):
    dev, _run, rec = _capture_env(reg, lite_exe)
    dev.fail_restore = True
    with pytest.raises(CaptureError, match="restore failed"):
        capture_scene("vibes1", registry=reg, room=room, devices=dev, recall_fn=rec, confirmed=True, sleep=lambda s: None)
    assert reg.snapshots.load("vibes1") is not None         # the measurement is kept


def test_capture_many_continues_past_a_failure(reg, room, lite_exe):
    dev, _run, rec = _capture_env(reg, lite_exe)
    out = capture_many(["vibes1", "nope", "movie"], registry=reg, room=room, devices=dev, recall_fn=rec,
                       confirmed=True, sleep=lambda s: None)
    assert isinstance(out["nope"], CaptureError) and out["vibes1"]["scene"] == "vibes1" and out["movie"]["scene"] == "movie"


# ---- taste profile -----------------------------------------------------------------
def snaps(reg, room):
    return reg.all_snapshots(room, IPS)


def test_profile_from_saved_scenes_excludes_agent_and_animated(reg, room):
    reg.snapshots.save({**reg.derive_snapshot(room, "movie", IPS), "static": False})
    p = scene_taste.generate_profile(snaps(reg, room), now=NOW)
    assert p["scenes"] == ["vibes1"] and p["scene_count"] == 1       # movie animated, agent.rainy/ha-only excluded
    vs = reg.derive_snapshot(room, "vibes1", IPS)["descriptors"]
    assert p["brightness"] == {"min": vs["mean_brightness"], "median": vs["mean_brightness"], "max": vs["mean_brightness"]}
    assert p["hues"] == [{"family": "purple", "share": 1.0}]  and p["kelvin"]["median"] == 2500     # mean of 3500 / 2400 / 2400 / 2700 over lit whites
    assert p["generated_at"] == "2026-10-18T20:00:00Z" and "overrides" not in p


def test_profile_statistics_over_several_scenes(room):
    def snap(mean, peak, kmean, hue, contrast, static=True):
        return {"static": static, "descriptors": {"lit_count": 1, "mean_brightness": mean, "peak_brightness": peak,
                                                  "kelvin_mean": kmean, "dominant_hue": hue, "zone_contrast": contrast}}
    s = {"a": snap(0.2, 0.5, 2200, "white", 0.1), "b": snap(0.4, 0.9, 2600, "purple", 0.3),
         "c": snap(0.6, 0.7, None, "white", 0.2), "d": snap(0.9, 1.0, 5000, "red", 0.9, static=False),
         "agent.x": snap(1, 1, 9000, "red", 1)}
    p = scene_taste.generate_profile(s, now=NOW)
    assert p["scenes"] == ["a", "b", "c"]
    assert p["brightness"] == {"min": 0.2, "median": 0.4, "max": 0.6}
    assert p["peak_brightness"] == 0.9
    assert p["kelvin"] == {"min": 2200, "median": 2400, "max": 2600}       # scene c has no whites
    assert p["hues"] == [{"family": "white", "share": 0.6667}, {"family": "purple", "share": 0.3333}]
    assert p["contrast"] == {"min": 0.1, "median": 0.2, "max": 0.3}


def test_empty_profile(room):
    p = scene_taste.generate_profile({}, now=NOW)
    assert p["scene_count"] == 0 and p["brightness"] is None and p["kelvin"] is None and p["hues"] == []
    assert scene_taste.prompt_text(p) == ""


def test_profile_validates_against_its_schema(reg, room):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    c = os.path.join(os.path.dirname(os.path.dirname(HERE)), "contracts")
    r = Registry()
    for f in ("taste-profile", "common"):
        r = r.with_resource(f + ".schema.json", Resource.from_contents(json.load(open(os.path.join(c, f + ".schema.json")))))
    v = Draft202012Validator(json.load(open(os.path.join(c, "taste-profile.schema.json"))), registry=r)
    p = scene_taste.generate_profile(snaps(reg, room), {"strength": 0.5}, now=NOW)
    assert not list(v.iter_errors(p)) and not list(v.iter_errors(scene_taste.generate_profile({})))


def test_overrides_win_and_survive_regeneration(reg, room, tmp_path):
    path = str(tmp_path / "taste.json")
    p = scene_taste.refresh_profile(path, snaps(reg, room), now=NOW)
    assert "overrides" not in p
    edited = json.load(open(path))
    edited["overrides"] = {"kelvin": {"max": 3000}, "strength": 0.5}
    json.dump(edited, open(path, "w"))
    p2 = scene_taste.refresh_profile(path, snaps(reg, room), now=NOW)
    assert p2["overrides"] == edited["overrides"]
    eff = scene_taste.apply_overrides(p2)
    assert eff["kelvin"]["max"] == 3000 and eff["strength"] == 0.5 and eff["kelvin"]["min"] == p2["kelvin"]["min"]
    assert "overrides" not in eff and p2["kelvin"]["max"] != 3000


def test_prompt_text_is_plain_statistics_and_can_be_switched_off(reg, room):
    p = scene_taste.generate_profile(snaps(reg, room), now=NOW)
    t = scene_taste.prompt_text(p)
    assert "2 of the user's saved scenes" in t and "Scene brightness" in t and "purple 50%" in t and "K" in t
    assert scene_taste.prompt_text(p, enabled=False) == ""
    assert scene_taste.prompt_text(scene_taste.generate_profile(snaps(reg, room), {"strength": 0})) == ""


def test_read_profile_tolerates_missing_or_corrupt(tmp_path):
    assert scene_taste.read_profile(str(tmp_path / "none.json")) is None
    (tmp_path / "bad.json").write_text("[1]")
    assert scene_taste.read_profile(str(tmp_path / "bad.json")) is None


def test_capture_and_profile_write_only_inside_scene_agent_storage(reg, room, lite_exe, tmp_path):
    """Spec §14.7: the scenes file (lite's) is never modified; only snapshots/ and the taste file are written."""
    before = open(reg.scenes_path, "rb").read(), os.stat(reg.scenes_path).st_mtime_ns
    dev, _run, rec = _capture_env(reg, lite_exe)
    capture_scene("vibes1", registry=reg, room=room, devices=dev, recall_fn=rec, confirmed=True, sleep=lambda s: None)
    scene_taste.refresh_profile(str(tmp_path / "registry" / "taste_profile.json"), snaps(reg, room))
    assert (open(reg.scenes_path, "rb").read(), os.stat(reg.scenes_path).st_mtime_ns) == before
    written = sorted(os.path.relpath(os.path.join(d, f), tmp_path) for d, _s, fs in os.walk(tmp_path) for f in fs)
    assert written == ["lite", "registry/taste_profile.json", "scenes.json", "snaps/vibes1.json"]
