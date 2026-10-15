# §5.3 console semantics as pure functions: every rule, no I/O (M4).

import pytest

import console as C
from conftest import assert_valid
from console import ConsoleError, ConsoleState, StrobeLimiter, apply, resolve
from outputs.base import HSB

# Bank A: ch1 = lr-all (tv-l, tv-r, couch), ch2 = tv (tv-l, tv-r), ch3 = couch, ch4 unbound.
BANKS = {"A": ["lr-all", "tv", "couch", None, None, None, None, None],
         "B": ["accent", None, None, None, None, None, None, None]}
MEMBERS = {"lr-all": ["tv-l", "tv-r", "couch"], "tv": ["tv-l", "tv-r"], "couch": ["couch"],
           "accent": ["lamp"]}


def no_limits(_fid):
    return 1.0, 3.0


def st_():
    return ConsoleState.default(BANKS)


def go(st, src="ui", **intent):
    return apply(st, intent, src)


def fader(st, ch, v, src="ui"):
    return go(st, src, intent="fader", ch=ch, v=v)


def out(st, limits=no_limits):
    return resolve(st, MEMBERS, limits)


# ---- validation -----------------------------------------------------------------------------

@pytest.mark.parametrize("intent, msg", [
    ({"intent": "nope"}, "unknown intent"),
    ({"intent": "who", "who": "board"}, "not a console intent"),
    ({"intent": "fader", "ch": 9, "v": 0.5}, "ch must be"),
    ({"intent": "fader", "ch": True, "v": 0.5}, "ch must be"),
    ({"intent": "fader", "ch": 1, "v": 1.2}, "0..1"),
    ({"intent": "fader", "ch": 1, "v": float("nan")}, "0..1"),
    ({"intent": "flash", "ch": 1, "down": 1}, "true or false"),
    ({"intent": "bank", "bank": "D"}, "A, B or C"),
    ({"intent": "fader_fn", "fn": "strobe"}, "level, hue or sat"),
    ({"intent": "palette_apply", "i": 8}, "0-7"),
    ({"intent": "cue_store", "i": -1}, "0-7"),
    ({"intent": "blackout", "on": "yes"}, "true or false"),
    ({"intent": "assign", "ch": 1, "group_id": ""}, "group id or null"),
])
def test_bad_intents_are_rejected_not_dropped(intent, msg):
    st = st_()
    before = st.to_state_fields()
    with pytest.raises(ConsoleError, match=msg):
        apply(st, intent, "ui")
    assert st.to_state_fields() == before


def test_bad_shapes_and_sources():
    with pytest.raises(ConsoleError, match="'intent' name"):
        apply(st_(), {"ch": 1}, "ui")
    with pytest.raises(ConsoleError, match="unknown src"):
        apply(st_(), {"intent": "blackout", "on": True}, "ai")


def test_hue_and_sat_need_exactly_one_of_delta_or_v_and_a_touched_channel():
    st = st_()
    with pytest.raises(ConsoleError, match="touch a channel first"):
        go(st, intent="hue", delta=1)
    fader(st, 1, 0.5)
    for bad in ({}, {"delta": 1, "v": 10}):
        with pytest.raises(ConsoleError, match="exactly one"):
            go(st, intent="hue", **bad)
    with pytest.raises(ConsoleError, match="integer"):
        go(st, intent="sat", delta=0.5)
    with pytest.raises(ConsoleError, match="0..360"):
        go(st, intent="hue", v=360)
    with pytest.raises(ConsoleError, match="0..1"):
        go(st, intent="sat", v=2)


# ---- HTP / LTP / master / blackout ----------------------------------------------------------

def test_level_is_htp_across_groups():
    st = st_()
    fader(st, 1, 0.3)                     # lr-all
    fader(st, 2, 0.8)                     # tv
    o = out(st)
    assert o["tv-l"].b == pytest.approx(0.8) and o["tv-r"].b == pytest.approx(0.8)
    assert o["couch"].b == pytest.approx(0.3)
    fader(st, 2, 0.1)                     # HTP: lr-all's 0.3 now wins on the TV bulbs
    assert out(st)["tv-l"].b == pytest.approx(0.3)


def test_hue_sat_is_ltp_across_groups():
    st = st_()
    fader(st, 1, 0.5)
    go(st, intent="hue", v=120)           # lr-all green
    go(st, intent="sat", v=1.0)
    fader(st, 2, 0.5)
    go(st, intent="hue", v=240)           # tv blue, touched last
    go(st, intent="sat", v=0.5)
    o = out(st)
    assert (o["tv-l"].h, o["tv-l"].s) == (240, 0.5) and (o["couch"].h, o["couch"].s) == (120, 1.0)
    go(st, intent="assign", ch=1)         # select lr-all, retouch its colour
    go(st, intent="hue", delta=2)
    assert out(st)["tv-l"].h == pytest.approx(130)


def test_level_moves_do_not_steal_ltp_colour():
    st = st_()
    fader(st, 2, 0.5)
    go(st, intent="hue", v=240)
    fader(st, 1, 0.5)
    go(st, intent="hue", v=120)           # lr-all colour last
    fader(st, 2, 0.9)                     # tv level move: colour stays lr-all's
    assert out(st)["tv-l"].h == 120


def test_master_scales_and_blackout_forces_zero_even_over_flash():
    st = st_()
    fader(st, 3, 0.8)
    fader(st, "master", 0.5)
    assert out(st)["couch"].b == pytest.approx(0.4)
    go(st, intent="flash", ch=3, down=True)
    assert out(st)["couch"].b == pytest.approx(0.5)      # flash = 1.0, then x master
    go(st, intent="blackout", on=True)
    assert all(v.b == 0 for v in out(st).values())
    go(st, intent="blackout", on=False)
    assert out(st)["couch"].b == pytest.approx(0.5)


def test_unbound_fixtures_and_channels_hold():
    st = st_()
    fader(st, 4, 1.0)                     # ch4 has no group
    assert set(out(st)) == {"tv-l", "tv-r", "couch", "lamp"}
    assert "fridge" not in out(st)        # in no bound group: never pushed (D16)


def test_other_banks_stay_live():
    st = st_()
    go(st, intent="bank", bank="B")
    fader(st, 1, 0.7)                     # accent
    go(st, intent="bank", bank="A")
    assert out(st)["lamp"].b == pytest.approx(0.7)


# ---- safety caps (D8, §8.2) -------------------------------------------------------------------

def test_max_level_caps_after_master_and_flash_cannot_exceed():
    st = st_()
    caps = {"tv-l": (0.6, 3.0)}

    def limits(fid):
        return caps.get(fid, (1.0, 3.0))
    fader(st, 2, 1.0)
    assert out(st, limits)["tv-l"].b == pytest.approx(0.6)
    assert out(st, limits)["tv-r"].b == pytest.approx(1.0)
    go(st, intent="flash", ch=2, down=True)
    assert out(st, limits)["tv-l"].b == pytest.approx(0.6)
    fader(st, "master", 0.5)
    assert out(st, limits)["tv-l"].b == pytest.approx(0.5)  # below the cap after master


# ---- flash ------------------------------------------------------------------------------------

def test_flash_is_full_at_the_channels_colour_and_returns_on_release():
    st = st_()
    fader(st, 1, 0.2)
    go(st, intent="hue", v=120)
    fader(st, 2, 0.0)
    go(st, intent="hue", v=0)
    go(st, intent="sat", v=1.0)
    go(st, intent="assign", ch=1)
    go(st, intent="hue", v=120)           # lr-all colour touched last
    go(st, intent="flash", ch=2, down=True)
    o = out(st)
    assert o["tv-l"].b == 1.0 and o["tv-l"].h == 0 and o["couch"].b == pytest.approx(0.2)
    assert st.to_state_fields()["channels"][1]["flash"] is True
    go(st, intent="flash", ch=2, down=False)
    assert out(st)["tv-l"].b == pytest.approx(0.2)
    assert st.channel("A", 2).level == 0.0               # the fader value was never changed


def test_flash_ignores_the_fader_and_touches_last_touched():
    st = st_()
    go(st, intent="flash", ch=3, down=True)
    assert st.last_touched == ("A", 3)
    fader(st, 3, 0.1)
    assert out(st)["couch"].b == 1.0


# ---- pickup -----------------------------------------------------------------------------------

def test_board_fader_is_ignored_until_it_crosses_the_stored_value():
    st = st_()
    fader(st, 1, 0.6)                     # UI sets 0.6; no board seen yet
    fader(st, 1, 0.1, "board")            # first sight, far away: never jump
    assert st.channel("A", 1).level == 0.6 and st.channel("A", 1).engaged is False
    fader(st, 1, 0.4, "board")
    assert st.channel("A", 1).level == 0.6
    fader(st, 1, 0.7, "board")            # crossed 0.6: engaged, value applies
    assert st.channel("A", 1).engaged and st.channel("A", 1).level == 0.7
    fader(st, 1, 0.3, "board")
    assert st.channel("A", 1).level == 0.3


def test_board_fader_engages_within_tolerance():
    st = st_()
    fader(st, 1, 0.5)
    fader(st, 1, 0.9, "board")
    assert not st.channel("A", 1).engaged
    fader(st, 1, 0.515, "board")          # within 0.02 without crossing
    assert st.channel("A", 1).engaged and st.channel("A", 1).level == 0.515


def test_bank_and_fn_switch_disengage_and_ui_engages_immediately():
    st = st_()
    fader(st, 1, 0.5, "board")            # engaged at 0.5 (matches? no: stored 0 -> first sight far)
    fader(st, 1, 0.0, "board")
    fader(st, 1, 0.5, "board")
    assert st.channel("A", 1).engaged and st.channel("A", 1).level == 0.5
    go(st, intent="bank", bank="B")
    assert not any(c.engaged for c in st.channels if c.bank != "B")
    assert not st.channel("B", 1).engaged                # physical fader 1 at 0.5, accent at 0
    fader(st, 1, 0.9, "board")
    assert st.channel("B", 1).level == 0.0               # no jump
    fader(st, 1, 0.3, "ui")                              # UI drag applies at once
    assert st.channel("B", 1).level == 0.3
    go(st, intent="fader_fn", fn="hue")
    assert not st.channel("B", 1).engaged
    go(st, intent="bank", bank="B")                      # same bank: no change
    go(st, intent="fader_fn", fn="hue")


def test_bank_switch_engages_a_fader_that_already_matches():
    st = st_()
    fader(st, 1, 0.0, "board")            # physical fader 1 at 0, all channels at 0
    go(st, intent="bank", bank="B")
    assert st.channel("B", 1).engaged


def test_hue_and_sat_fader_functions():
    st = st_()
    go(st, intent="fader_fn", fn="hue")
    fader(st, 2, 0.5)
    assert st.channel("A", 2).hue == 180 and st.channel("A", 2).level == 0
    go(st, intent="fader_fn", fn="sat")
    fader(st, 2, 0.25)
    assert st.channel("A", 2).sat == 0.25
    go(st, intent="fader_fn", fn="hue")
    fader(st, 2, 1.0)
    assert st.channel("A", 2).hue == 0                   # 360 wraps to 0
    fader(st, 2, 0.5, "board")                           # pickup compares in the fn's units
    assert st.channel("A", 2).hue == 0 and not st.channel("A", 2).engaged
    fader(st, 2, 0.01, "board")
    assert st.channel("A", 2).engaged


def test_stored_value_moving_under_a_board_fader_disengages_it():
    st = st_()
    fader(st, 1, 0.0, "board")
    assert st.channel("A", 1).engaged
    fader(st, 1, 0.8, "ui")
    assert not st.channel("A", 1).engaged                # ghost marker at 0.8, board still at 0


# ---- encoders, palette, assign ------------------------------------------------------------------

def test_encoder_detents_wrap_hue_and_clamp_sat():
    st = st_()
    fader(st, 1, 0.5)
    go(st, intent="hue", delta=-1)
    assert st.channel("A", 1).hue == 355
    go(st, intent="sat", delta=10)
    assert st.channel("A", 1).sat == pytest.approx(0.2)
    go(st, intent="sat", delta=-100)
    assert st.channel("A", 1).sat == 0


def test_palette_apply_and_store_act_on_last_touched():
    st = st_()
    with pytest.raises(ConsoleError, match="touch a channel"):
        go(st, intent="palette_apply", i=0)
    fader(st, 3, 0.5)
    go(st, intent="palette_apply", i=5)                  # #3b82f6
    c = st.channel("A", 3)
    sw = HSB.from_rgb((0x3b, 0x82, 0xf6))
    assert (c.hue, c.sat) == pytest.approx((sw.h, sw.s))
    go(st, intent="hue", v=0)
    go(st, intent="sat", v=1)
    go(st, intent="palette_store", i=7)
    assert st.palette[7] == "#ff0000"


def test_assign_selects_rebinds_and_clears():
    st = st_()
    assert go(st, intent="assign", ch=4) == [] and st.last_touched == ("A", 4)
    assert go(st, intent="assign", ch=4, group_id="couch") == [("assign", "A", 4, "couch")]
    fader(st, 4, 0.9)
    assert out(st)["couch"].b == pytest.approx(0.9)
    assert go(st, intent="assign", ch=4, group_id=None) == [("assign", "A", 4, None)]
    assert st.channel("A", 4).group_id is None


# ---- cues ---------------------------------------------------------------------------------------

def test_cue_store_and_recall_restore_all_24_channels_not_master():
    st = st_()
    fader(st, 1, 0.4)
    go(st, intent="hue", v=200)
    go(st, intent="bank", bank="B")
    fader(st, 1, 0.9)
    go(st, intent="cue_store", i=2)
    assert st.cue_active == 2 and st.to_state_fields()["cues"][2]["stored"]
    look = [c.look() for c in st.channels]
    fader(st, 1, 0.0)
    assert st.cue_active is None                         # a manual change leaves the cue
    fader(st, "master", 0.3)
    go(st, intent="bank", bank="A")
    fader(st, 1, 1.0)
    assert go(st, intent="cue_recall", i=2) == [("recall", 2, 0)]
    assert [c.look() for c in st.channels] == look
    assert st.master == 0.3 and st.bank == "A" and st.cue_active == 2


def test_recalling_an_empty_cue_is_an_error():
    st = st_()
    with pytest.raises(ConsoleError, match="cue 4 \\(Chill\\) is empty"):
        go(st, intent="cue_recall", i=3)


def test_cue_recall_colour_ties_break_by_level():
    st = st_()
    fader(st, 1, 0.2)
    go(st, intent="hue", v=120)
    fader(st, 2, 0.9)
    go(st, intent="hue", v=240)
    go(st, intent="assign", ch=1)
    go(st, intent="hue", v=120)                          # lr-all touched last
    go(st, intent="cue_store", i=0)
    go(st, intent="hue", v=60)
    go(st, intent="cue_recall", i=0)                     # all channels touched together
    assert out(st)["tv-l"].h == 240                      # the brighter channel's colour


def test_cue_recall_disengages_board_faders_that_no_longer_match():
    st = st_()
    fader(st, 1, 0.0, "board")
    fader(st, 1, 0.5, "board")
    go(st, intent="cue_store", i=0)
    fader(st, 1, 0.9, "board")
    go(st, intent="cue_recall", i=0)
    assert st.channel("A", 1).level == 0.5 and not st.channel("A", 1).engaged


def test_cue_fade_ms_is_reported():
    st = st_()
    go(st, intent="cue_store", i=1)
    st.cues[1].fade_ms = 800
    assert go(st, intent="cue_recall", i=1) == [("recall", 1, 800)]


# ---- D2: switching to BOARD -------------------------------------------------------------------

ART = {"tv-l": HSB(0, 1, 0.6), "tv-r": HSB(240, 1, 0.4), "couch": HSB(120, 0.5, 0.8)}


def test_board_seed_pushes_the_art_look_unchanged():
    st = st_()
    fader(st, 1, 1.0)                     # stale console values from earlier
    go(st, intent="hue", v=300)
    C.seed_from_art(st, ART, MEMBERS)
    o = out(st)
    for fid, art in ART.items():
        assert (o[fid].h, o[fid].s, o[fid].b) == pytest.approx((art.h, art.s, art.b))
    assert "lamp" not in ART and o["lamp"].b == 0        # no art: plain channel values


def test_board_seed_shows_oklab_mean_and_mean_brightness_on_channels():
    st = st_()
    C.seed_from_art(st, ART, MEMBERS)
    tv = st.channel("A", 2)
    assert tv.level == pytest.approx(0.5)
    h, s = C.oklab.mean_hue_sat([ART["tv-l"], ART["tv-r"]])
    assert (tv.hue, tv.sat) == pytest.approx((h, s))
    assert 270 < tv.hue < 360                             # red + blue -> purple/magenta, not green
    assert st.channel("A", 3).hue == pytest.approx(120, abs=0.5)


def test_after_seed_a_level_move_takes_over_level_but_keeps_art_colour():
    st = st_()
    C.seed_from_art(st, ART, MEMBERS)
    fader(st, 3, 0.2)                                     # couch level
    o = out(st)
    assert o["couch"].b == pytest.approx(0.6)             # HTP: lr-all's seeded mean (0.6) wins
    assert o["couch"].h == pytest.approx(120)             # colour still art
    assert o["tv-l"].b == pytest.approx(0.6)              # untouched groups keep art brightness
    go(st, intent="hue", v=30)                            # couch colour now LTP
    assert out(st)["couch"].h == 30 and out(st)["tv-l"].h == 0


def test_flash_after_seed_overrides_art_level():
    st = st_()
    C.seed_from_art(st, ART, MEMBERS)
    go(st, intent="flash", ch=2, down=True)
    assert out(st)["tv-r"].b == 1.0


# ---- D7 strobe limit ----------------------------------------------------------------------------

def on_off(i):
    return HSB(0, 1, 1.0 if i % 2 == 0 else 0.0)


def edge_times(sent):
    """Times at which the light visibly changed (an edge against the previous value sent)."""
    out, last = [], None
    for t, v in sent:
        if C.is_edge(last, v):
            out.append(t)
        last = v
    return out


def test_strobe_limit_allows_6_edges_per_second_at_3hz_and_holds_the_newest():
    lim = StrobeLimiter()
    sent = [(i * 50, v) for i in range(12) if (v := lim.offer("f", on_off(i), i * 50, 3.0)) is not None]
    assert [t for t, _ in sent] == [0, 50, 100, 150, 200, 250, 300]   # first value + 6 edges
    assert lim.pending["f"][0] == on_off(11)             # newest held: "off" (light shows "on")
    assert lim.next_due_ms() == pytest.approx(50 + 1000)
    assert lim.poll(1049) == {}
    assert lim.poll(1050) == {"f": on_off(11)}
    assert lim.pending == {}


def test_strobe_limit_counts_in_any_sliding_second():
    lim = StrobeLimiter()
    sent = []
    for i in range(200):                                  # hammer at 20 Hz for 10 s
        t = i * 50
        if (v := lim.offer("f", on_off(i), t, 3.0)) is not None:
            sent.append((t, v))
        sent += [(t, v) for v in lim.poll(t).values()]
    edges = edge_times(sent)
    assert len(edges) >= 50                               # it still strobes, at the cap
    for a in edges:
        assert len([b for b in edges if a <= b < a + 1000]) <= 6


def test_small_changes_are_not_edges_and_pass():
    lim = StrobeLimiter()
    lim.offer("f", HSB(0, 1, 0.5), 0, 3.0)
    for i in range(20):                                   # a slow fade: 0.02 steps, never 0.10
        assert lim.offer("f", HSB(0, 1, 0.52 + 0.02 * (i % 5)), i, 3.0) is not None


def test_colour_jumps_are_edges_only_when_bright():
    assert C.is_edge(HSB(0, 1, 0.5), HSB(240, 1, 0.5))
    assert not C.is_edge(HSB(0, 1, 0.05), HSB(240, 1, 0.05))
    assert not C.is_edge(HSB(0, 1, 0.5), HSB(2, 1, 0.5))
    assert C.is_edge(HSB(0, 1, 0.5), HSB(0, 1, 0.6))      # exactly 0.10
    assert not C.is_edge(None, HSB(0, 1, 1))


def test_lower_flash_rates_and_the_3hz_ceiling():
    assert C.edge_budget(3.0) == (6, 1000.0)
    assert C.edge_budget(1.5) == (3, 1000.0)
    assert C.edge_budget(0.25) == (1, 2000.0)
    assert C.edge_budget(10.0) == (6, 1000.0)             # never above 3 Hz
    assert C.edge_budget(0) == (6, 1000.0)
    lim = StrobeLimiter()
    assert lim.offer("f", on_off(0), 0, 1.0) is not None
    assert lim.offer("f", on_off(1), 10, 1.0) is not None
    assert lim.offer("f", on_off(2), 20, 1.0) is not None
    assert lim.offer("f", on_off(3), 30, 1.0) is None     # 2 edges/s at 1 Hz
    assert lim.offer("g", on_off(1), 30, 1.0) is not None  # per fixture


def test_a_non_edge_value_replaces_a_held_one():
    lim = StrobeLimiter()
    for i in range(8):
        lim.offer("f", on_off(i), i, 3.0)
    assert "f" in lim.pending                             # held "off"
    assert lim.offer("f", HSB(0, 1, 0.95), 10, 3.0) is not None   # near the last sent "on"
    assert lim.pending == {}


# ---- State + console.json -------------------------------------------------------------------------

def test_state_fields_and_console_file_validate_and_round_trip():
    st = st_()
    fader(st, 2, 0.75)
    go(st, intent="hue", v=200.5)
    go(st, intent="cue_store", i=4)
    st.cues[4].fade_ms = 500
    go(st, intent="bank", bank="C")
    go(st, intent="fader_fn", fn="sat")
    fader(st, "master", 0.8)
    go(st, intent="blackout", on=True)
    go(st, intent="flash", ch=1, down=True)
    d = C.to_file(st)
    assert_valid("console", d)
    st2 = C.from_file(d, BANKS)
    assert C.to_file(st2) == d
    assert st2.blackout is False and not any(c.flash for c in st2.channels)
    assert all(c.engaged for c in st2.channels)          # no board position known yet
    assert st2.channel("A", 1).group_id == "lr-all"


def test_console_file_tolerates_junk():
    st = C.from_file({"bank": "Z", "fader_fn": "x", "master": 7, "palette": ["#000000"]}, BANKS)
    assert (st.bank, st.fader_fn, st.master) == ("A", "level", 1.0)
    assert st.palette == C.DEFAULT_PALETTE
