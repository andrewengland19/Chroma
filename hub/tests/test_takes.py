# takes.py pure logic: D9 resolved events, D10 modes, replayer timing, activity spans,
# the take store, and replay through console.apply independent of the live view.

import json
import os

import pytest

import console as C
from conftest import assert_valid
from console import ConsoleState
from takes import (Recorder, Replayer, TakeError, TakeStore, activity_lanes, read_take, resolve_event,
                   touch_key)

BANKS = {"A": ["lr-all", "bed", "counter", "playbill", "lr-l", "lr-r", "accent", "fx"],
         "B": ["tv", "couch", "lamp", None, None, None, None, None]}


def st_():
    return ConsoleState.default(BANKS)


def live(st, intent, src="ui"):
    C.apply(st, intent, src)
    return resolve_event(st, intent, src)


# ---- D9: resolved b/c/a/v ------------------------------------------------------------------

def test_resolved_events_name_the_channel_attribute_and_absolute_value():
    st = st_()
    assert live(st, {"intent": "fader", "ch": 3, "v": 0.62}) == {"g": "counter", "b": "A", "c": 3, "a": "level", "v": 0.62}
    assert live(st, {"intent": "flash", "ch": 3, "down": True})["a"] == "flash"
    assert live(st, {"intent": "fader", "ch": "master", "v": 0.8}) == {"c": "master", "a": "master", "v": 0.8}
    assert live(st, {"intent": "hue", "delta": 3}) == {"g": "counter", "b": "A", "c": 3, "a": "hue", "v": 15.0}
    assert live(st, {"intent": "sat", "v": 0.5})["v"] == 0.5
    assert live(st, {"intent": "palette_apply", "i": 4}) == {"g": "counter", "b": "A", "c": 3, "a": "palette",
                                                               "v": st.palette[4]}
    assert live(st, {"intent": "bank", "bank": "B"}) == {"b": "B", "a": "bank", "v": "B"}
    assert live(st, {"intent": "fader_fn", "fn": "hue"}) == {"a": "fader_fn", "v": "hue"}
    ev = live(st, {"intent": "fader", "ch": 2, "v": 0.5})               # bank B, HUE function
    assert ev == {"g": "couch", "b": "B", "c": 2, "a": "hue", "v": 180.0}
    assert live(st, {"intent": "assign", "ch": 4})["a"] == "select"
    assert live(st, {"intent": "assign", "ch": 4, "group_id": "tv"})["v"] == "tv"
    assert live(st, {"intent": "blackout", "on": True}) == {"a": "blackout", "v": True}
    C.apply(st, {"intent": "cue_store", "i": 2})
    assert live(st, {"intent": "cue_recall", "i": 2}) == {"a": "cue", "v": 2}


def test_editing_and_unpicked_board_moves_are_not_recorded():
    st = st_()
    C.apply(st, {"intent": "fader", "ch": 1, "v": 0.5})
    assert live(st, {"intent": "palette_store", "i": 0}) is None
    assert live(st, {"intent": "cue_store", "i": 0}) is None
    assert resolve_event(st, {"intent": "who", "who": "board"}, "ui") is None
    assert live(st, {"intent": "fader", "ch": 1, "v": 0.9}, src="board") is None   # far away: no pickup
    assert live(st, {"intent": "fader", "ch": 1, "v": 0.51}, src="board")["v"] == 0.51


def test_replay_applies_the_resolved_target_whatever_the_live_view():
    rec = st_()
    ev = live(rec, {"intent": "fader", "ch": 2, "v": 0.7})              # A2 level
    hue = (C.apply(rec, {"intent": "hue", "v": 200}), resolve_event(rec, {"intent": "hue", "v": 200}, "ui"))[1]
    st = st_()
    C.apply(st, {"intent": "bank", "bank": "B"})
    C.apply(st, {"intent": "fader_fn", "fn": "sat"})
    C.apply(st, {"intent": "fader", "ch": 1, "v": 0.3})                 # the user's selection: B1
    C.apply(st, {"intent": "fader", "ch": 2, "v": 0.7}, "replay", at=ev)
    C.apply(st, {"intent": "hue", "v": 200}, "replay", at=hue)
    a2 = st.channel("A", 2)
    assert a2.level == 0.7 and a2.hue == 200.0
    assert st.channel("B", 2).level == 0.0 and st.channel("B", 2).sat == 0.0
    assert (st.bank, st.fader_fn, st.last_touched) == ("B", "sat", ("B", 1))  # view untouched
    for a in ("bank", "fader_fn", "select", "assign"):                       # skipped on replay
        assert C.apply(st, {"intent": "bank", "bank": "A"}, "replay", at={"a": a, "v": "A", "b": "A", "c": 1}) == []
    assert st.bank == "B" and st.channel("A", 1).group_id == "lr-all"


def test_replayed_cue_and_blackout_and_bad_events():
    st = st_()
    C.apply(st, {"intent": "fader", "ch": 1, "v": 0.4})
    C.apply(st, {"intent": "cue_store", "i": 5})
    C.apply(st, {"intent": "fader", "ch": 1, "v": 0.9})
    assert C.apply(st, {"intent": "cue_recall", "i": 5}, "replay", at={"a": "cue", "v": 5}) == [("recall", 5, 0)]
    assert st.channel("A", 1).level == 0.4
    C.apply(st, {"intent": "blackout", "on": True}, "replay", at={"a": "blackout", "v": True})
    assert st.blackout
    with pytest.raises(C.ConsoleError):
        C.apply(st, {"intent": "fader", "ch": 1, "v": 1}, "replay", at={"a": "level", "v": 2.0, "b": "A", "c": 1})
    with pytest.raises(C.ConsoleError):
        C.apply(st, {"intent": "fader", "ch": 1, "v": 1}, "replay", at={"a": "level", "v": 0.5})


def test_load_init_restores_the_look_and_releases_flashes():
    st = st_()
    C.apply(st, {"intent": "fader", "ch": 1, "v": 0.6})
    C.apply(st, {"intent": "fader", "ch": "master", "v": 0.7})
    init = C.snapshot_init(st)
    C.apply(st, {"intent": "fader", "ch": 1, "v": 0.1})
    C.apply(st, {"intent": "flash", "ch": 2, "down": True})
    C.apply(st, {"intent": "bank", "bank": "B"})
    C.load_init(st, init)
    assert st.channel("A", 1).level == 0.6 and st.master == 0.7 and not st.channel("A", 2).flash
    assert st.bank == "B"


# ---- D10 modes ---------------------------------------------------------------------------

def ev(p, ch, a, v, b="A", g=None):
    i = {"intent": "flash", "ch": ch, "down": v} if a == "flash" else {"intent": "fader", "ch": ch, "v": v}
    return {"p": p, "w": p, "i": i, "g": g or f"g{ch}", "b": b, "c": ch, "a": a, "v": v}


OLD = [ev(1000, 1, "level", 0.2), ev(2000, 1, "level", 0.3), ev(3000, 1, "level", 0.4),
       ev(2000, 2, "level", 0.9), ev(5000, 1, "level", 0.5)]


def rec(mode, old=OLD):
    r = Recorder(header={"id": "x"}, mode=mode, old=list(old))
    r.add(1900, 1, {"intent": "fader", "ch": 1, "v": 0.8}, {"g": "g1", "b": "A", "c": 1, "a": "level", "v": 0.8})
    r.add(2100, 2, {"intent": "fader", "ch": 1, "v": 0.7}, {"g": "g1", "b": "A", "c": 1, "a": "level", "v": 0.7})
    return r


def test_replace_keeps_only_the_new_events():
    assert [(e["p"], e["v"]) for e in rec("replace").finish()] == [(1900, 0.8), (2100, 0.7)]


def test_merge_adds_without_removing():
    out = rec("merge").finish()
    assert len(out) == 7 and [e["p"] for e in out] == sorted(e["p"] for e in out)


def test_overdub_punches_in_only_the_touched_channel_for_its_span():
    r = rec("overdub")
    assert r.spans[("A", 1)] == [[1900, 2850]]                       # 2100 + 750 extends the first
    assert r.touched(("A", 1), 2500) and not r.touched(("A", 1), 2900) and not r.touched(("A", 2), 2000)
    out = [(e["p"], e["c"], e["v"]) for e in r.finish()]
    assert out == [(1000, 1, 0.2), (1900, 1, 0.8), (2000, 2, 0.9), (2100, 1, 0.7), (3000, 1, 0.4), (5000, 1, 0.5)]


def test_overdub_releases_a_flash_the_old_take_held_into_the_span():
    old = [ev(1000, 3, "flash", True), ev(2400, 3, "flash", False), ev(4000, 3, "flash", True), ev(4200, 3, "flash", False)]
    r = Recorder(header={"id": "x"}, mode="overdub", old=old)
    r.add(2000, 1, {"intent": "fader", "ch": 3, "v": 0.5}, {"g": "g3", "b": "A", "c": 3, "a": "level", "v": 0.5})
    out = [(e["p"], e["a"], e["v"]) for e in r.finish()]
    assert out == [(1000, "flash", True), (2000, "flash", False), (2000, "level", 0.5),
                   (4000, "flash", True), (4200, "flash", False)]


def test_overdub_release_when_the_old_release_was_punched_out_and_nothing_follows():
    old = [ev(1000, 3, "flash", True), ev(1500, 3, "flash", False)]
    r = Recorder(header={"id": "x"}, mode="overdub", old=old)
    r.add(1200, 1, {"intent": "fader", "ch": 3, "v": 0.5}, {"g": "g3", "b": "A", "c": 3, "a": "level", "v": 0.5})
    assert [(e["p"], e["a"], e["v"]) for e in r.finish()] == [(1000, "flash", True), (1200, "flash", False),
                                                              (1200, "level", 0.5)]


def test_touch_keys():
    assert touch_key(ev(0, 3, "level", 0.1)) == ("A", 3)
    assert touch_key({"c": "master", "a": "master"}) == "master"
    assert touch_key({"a": "blackout"}) == "blackout"


# ---- replayer ---------------------------------------------------------------------------

def test_replayer_due_seek_and_nudge():
    rp = Replayer(OLD)
    assert [e["p"] for e in rp.due(1999)] == [1000]
    assert [e["p"] for e in rp.due(2000)] == [2000, 2000]
    assert rp.next_due() == 3000
    assert [e["p"] for e in rp.seek(2500)] == [1000, 2000, 2000]
    assert rp.next_due() == 3000
    rp = Replayer(OLD, nudge_ms=40, offset_ms=-10)                    # +30 ms: later
    assert rp.due(1029) == [] and len(rp.due(1030)) == 1
    assert rp.next_due() == 2030
    rp.seek(10_000)
    assert rp.next_due() is None and rp.due(20_000) == []


# ---- activity strip ---------------------------------------------------------------------

def test_activity_lanes_are_bars_per_group_in_lane_order():
    evs = [ev(1000, 1, "level", 0.2, g="lr-all"), ev(1200, 1, "level", 0.3, g="lr-all"),
           ev(5000, 1, "level", 0.4, g="lr-all"), ev(2000, 8, "flash", True, g="fx"), ev(2600, 8, "flash", False, g="fx"),
           {"p": 900, "w": 0, "i": {"intent": "assign", "ch": 2}, "g": "bed", "b": "A", "c": 2, "a": "select", "v": None}]
    lanes = activity_lanes(evs, {"lr-all": {"name": "LR ALL", "color": "#e5484d"}}, ["fx", "lr-all"])
    assert [l["group_id"] for l in lanes] == ["fx", "lr-all"]
    assert lanes[0]["spans"] == [[2000, 2600]] and lanes[0]["color"] == "#888888"
    assert lanes[1]["spans"] == [[1000, 1350], [5000, 5150]]


# ---- store ------------------------------------------------------------------------------

def header(tid="20261016-120000", **kw):
    h = {"v": 1, "id": tid, "name": "Older — draft 01", "track_id": "abc", "title": "Older",
         "artist": "Sasha Alex Sloan", "album": "Loser - EP", "dur_ms": 205000, "fixtures_rev": 1,
         "nudge_ms": 0, "training": True, "markers": [], "created_ms": 1, "performer": "You",
         "sync_err_ms": 3.0, "init": C.snapshot_init(st_()), "groups": {"counter": {"name": "COUNTER", "color": "#f2c94c"}}}
    h.update(kw)
    return h


def test_store_round_trip_list_rename_flags_markers_duplicate_delete(tmp_path):
    s = TakeStore(str(tmp_path / "takes"))
    assert s.list() == []
    s.save(header(), [ev(2000, 3, "level", 0.5, g="counter"), ev(1000, 3, "level", 0.4, g="counter")])
    path = s.path("20261016-120000")
    assert os.path.basename(path) == "20261016-120000_older.jsonl"
    h, evs = read_take(path)
    assert [e["p"] for e in evs] == [1000, 2000]                       # sorted on save
    assert h["stats"] == {"events": 2, "groups_used": ["counter"], "length_ms": 205000}
    assert_valid("take-header", h)
    for e in evs:
        assert_valid("take-event", e)
    s.update_header("20261016-120000", lambda h: h.__setitem__("name", "Final"))
    assert s.list()[0]["name"] == "Final" and s.list()[0]["events"] == 2
    new = s.duplicate("20261016-120000", 1_791_600_000)
    assert new != "20261016-120000" and s.get(new)[0]["name"] == "Final copy"
    s.delete(new)
    assert new not in s.paths() and os.listdir(tmp_path / "takes" / ".deleted")
    with pytest.raises(TakeError):
        s.get(new)


def test_new_ids_never_collide(tmp_path):
    s = TakeStore(str(tmp_path))
    a = s.new_id(1_791_600_000)
    s.save(header(a), [])
    b = s.new_id(1_791_600_000)
    assert b == a + "-1"


def test_a_corrupt_take_is_skipped_in_the_list(tmp_path):
    s = TakeStore(str(tmp_path))
    s.save(header(), [])
    with open(tmp_path / "20261016-120001_x.jsonl", "w") as fh:
        fh.write("{not json\n")
    assert [t["id"] for t in s.list()] == ["20261016-120000"]


def test_example_take_file_validates():
    root = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "examples", "take", "ok-older.jsonl")
    h, evs = read_take(root)
    assert h["init"]["channels"][2]["group_id"] == "counter" and len(evs) == 3
    assert json.dumps(h)
