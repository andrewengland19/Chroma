# =============================================================================
# Scene Agent M1 (lighting-scene-agent-spec §13): the room loader, the SceneSpec
# validator and the deterministic renderer. Golden files: every
# tests/data/scene/specs/<name>.json renders to exactly golden/<name>.json against
# the placeholder room in tests/data/scene. Regenerate after an intended change:
#   UPDATE_GOLDEN=1 python -m pytest tests/test_scene_render.py
# =============================================================================

import copy
import glob
import json
import os

import pytest

import room_model
import scene_render
from room_model import RoomError, build_room, load_room
from scene_render import SpecError, render, validate_spec

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "scene")
CONTRACTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "contracts", "examples")
SPECS = sorted(glob.glob(os.path.join(DATA, "specs", "*.json")))


def load(path):
    with open(path) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def room():
    return load_room(os.path.join(DATA, "room.yaml"), os.path.join(DATA, "fixtures.json"))


@pytest.fixture(scope="module")
def snapshots():
    return load(os.path.join(DATA, "snapshots.json"))


def _fixtures():
    return load(os.path.join(DATA, "fixtures.json"))["fixtures"]


# ---- golden files ------------------------------------------------------------------
@pytest.mark.parametrize("path", SPECS, ids=os.path.basename)
def test_golden(path, room, snapshots):
    out = render(room, load(path), snapshots)
    golden = os.path.join(DATA, "golden", os.path.basename(path))
    if os.environ.get("UPDATE_GOLDEN"):
        with open(golden, "w") as f:
            json.dump(out, f, indent=2, sort_keys=True)
            f.write("\n")
    assert out == load(golden)


def test_every_spec_has_a_golden():
    assert {os.path.basename(p) for p in SPECS} == \
        {os.path.basename(p) for p in glob.glob(os.path.join(DATA, "golden", "*.json"))}


def test_render_is_pure(room, snapshots):
    spec = load(os.path.join(DATA, "specs", "rainy-evening.json"))
    before = (copy.deepcopy(spec), copy.deepcopy(snapshots))
    assert render(room, spec, snapshots) == render(room, spec, snapshots)
    assert (spec, snapshots) == before


# ---- room model ----------------------------------------------------------------------
def test_room_reuses_fixture_positions(room):
    assert set(room.fixtures) == {"tv-left", "tv-right", "couch-left", "couch-right", "window-lamp", "pendant"}
    assert room.fixtures["tv-left"].position == pytest.approx((1.5, 0.42, 0.6))   # 0.3 * 5, 0.1 * 4.2
    assert room.fixtures["pendant"].position[2] == 2.3


def test_room_zones(room):
    assert room.zones_of("tv-left") == ["tv_side"]
    assert room.zones_of("couch-left") == ["couch", "seating"]
    assert room.zones_of("window-lamp") == ["window_side"]
    assert room.zones_of("pendant") == ["high"]


def test_contract_room_example_matches_test_room(room):
    r = build_room(load(os.path.join(CONTRACTS, "room", "ok-living-room.json")), _fixtures())
    assert r == room


def test_missing_room_file_gives_defaults(tmp_path):
    r = load_room(str(tmp_path / "none.yaml"), os.path.join(DATA, "fixtures.json"))
    assert r.dimensions == room_model.DEFAULT_DIMENSIONS and set(r.anchors) == {"ceiling", "floor"}
    assert r.fixtures["tv-left"].position[2] == room_model.DEFAULT_Z


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(CONTRACTS, "room", "bad-*.json"))),
                         ids=os.path.basename)
def test_loader_rejects_bad_room_examples(path):
    with pytest.raises(RoomError):
        build_room(load(path), _fixtures())


@pytest.mark.parametrize("room_doc", [
    {"fixtures": {"ghost": {"z": 1}}},                                 # not in fixtures.json
    {"fixtures": {"tv-left": {"aim": "bookshelf"}}},                   # unknown anchor
    {"zones": {"z1": {"within": {"anchor": "nowhere", "radius": 1}}}},
    {"anchors": {"seat": {"position": [1, 1, 1]}}, "zones": {"seat": {"fixtures": []}}},
    {"viewpoint": {"from": "seat", "facing": "tv"}},
])
def test_loader_rejects_bad_references(room_doc):
    with pytest.raises(RoomError):
        build_room(room_doc, _fixtures())


def test_loader_never_writes(tmp_path):
    bad = tmp_path / "fixtures.json"
    bad.write_text("{ not json")
    with pytest.raises(RoomError):
        load_room(str(tmp_path / "room.yaml"), str(bad))
    assert sorted(os.listdir(tmp_path)) == ["fixtures.json"]       # not moved aside like FixtureStore.load


# ---- SceneSpec validation (hand validator == schema) -----------------------------------
@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(CONTRACTS, "scene-spec", "bad-*.json"))),
                         ids=os.path.basename)
def test_validator_rejects_bad_examples(path):
    with pytest.raises(SpecError):
        validate_spec(load(path))


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(CONTRACTS, "scene-spec", "ok-*.json"))) + SPECS,
                         ids=os.path.basename)
def test_ok_specs_validate_and_render(path, room, snapshots):
    validate_spec(load(path))
    render(room, load(path), snapshots)


@pytest.mark.parametrize("layer", [
    {"op": "fill", "target": {"fixture": "desk"}, "brightness": 1},        # not_added: not in the room
    {"op": "fill", "target": {"zone": "bookshelf"}, "brightness": 1},
    {"op": "pool", "center": "bookshelf", "radius_m": 1, "brightness": 1},
    {"op": "wash", "surface": "bookshelf", "brightness": 1},
    {"op": "gradient", "from": "main_seat", "to": "main_seat", "colors": [{"kelvin": 2700}, {"kelvin": 4000}]},
])
def test_unknown_references_are_errors(layer, room):
    with pytest.raises(SpecError):
        render(room, {"name": "agent.x", "layers": [layer]})


def test_missing_base_snapshot_is_an_error(room):
    with pytest.raises(SpecError):
        render(room, {"name": "agent.x", "base": {"scene": "nope", "mode": "start_from"}, "layers": []}, {})


# ---- behaviour the goldens pin, stated as properties ------------------------------------
def _spec(name):
    return load(os.path.join(DATA, "specs", f"{name}.json"))


def test_empty_spec_is_dark(room):
    out = render(room, {"name": "agent.dark", "layers": []})
    assert all(v["brightness"] == 0 for v in out["fixtures"].values())


def test_relative_edit_dims_and_cools_tv_side_only(room, snapshots):
    """Acceptance §14.4: like vibes1 but cooler and dimmer near the TV."""
    spec = {"name": "agent.x", "base": {"scene": "vibes1", "mode": "start_from"}, "layers": _spec("scale-shift")["layers"][:2]}
    base = render(room, {"name": "agent.b", "base": spec["base"], "layers": []}, snapshots)["fixtures"]
    out = render(room, spec, snapshots)["fixtures"]
    for fid in ("tv-left", "tv-right"):
        assert out[fid]["brightness"] == pytest.approx(base[fid]["brightness"] * 0.4, abs=1e-4)
        assert out[fid]["kelvin"] == base[fid]["kelvin"] + 600
    for fid in ("couch-left", "couch-right", "window-lamp", "pendant"):
        assert out[fid] == base[fid]


def test_moving_a_fixture_changes_spatial_render(snapshots):
    """Acceptance §14.5: a new position re-renders without touching the spec."""
    doc = load(os.path.join(CONTRACTS, "room", "ok-living-room.json"))
    fixtures = _fixtures()
    spec = _spec("pool-smooth")
    before = render(build_room(doc, fixtures), spec)["fixtures"]["tv-left"]
    next(f for f in fixtures if f["id"] == "tv-left")["y"] = 0.7
    after = render(build_room(doc, fixtures), spec)["fixtures"]["tv-left"]
    assert after["brightness"] > before["brightness"]


def test_pool_falloffs_order(room):
    b = {f: render(room, _spec(f"pool-{f}"))["fixtures"] for f in ("smooth", "linear", "hard")}
    # hard lights everything inside the radius fully; linear and smooth never exceed it
    for fid, v in b["hard"].items():
        assert v["brightness"] in (0.0, 0.6)
        assert b["linear"][fid]["brightness"] <= v["brightness"]
        assert b["smooth"][fid]["brightness"] <= v["brightness"]


def test_whites_stay_white(room, snapshots):
    out = render(room, _spec("fill"))["fixtures"]
    assert all(v["sat"] == 0 and v["kelvin"] == 3000 for v in out.values())


def test_clamping_is_reported(room):
    out = render(room, _spec("clamp"))
    assert out["fixtures"]["couch-right"]["brightness"] == 0.6
    assert out["fixtures"]["window-lamp"]["sat"] == 0 and out["fixtures"]["window-lamp"]["kelvin"] == 6500
    assert out["fixtures"]["pendant"]["brightness"] == 1.0
    assert set(out["clamped"]) == {"couch-right", "window-lamp", "pendant"}


def test_layer_order_matters(room):
    a = render(room, _spec("order-fill-then-scale"))["fixtures"]
    b = render(room, _spec("order-scale-then-fill"))["fixtures"]
    assert all(v["brightness"] == 0.25 for v in a.values())
    assert all(v["brightness"] == 0.5 for v in b.values())


def test_wash_hits_by_aim(room):
    out = render(room, _spec("wash"))["fixtures"]
    assert out["tv-left"]["brightness"] == out["tv-right"]["brightness"] == 0.5     # aim: tv_wall
    assert out["couch-left"]["brightness"] == 0.3                                   # aim: up -> ceiling
    assert out["window-lamp"]["brightness"] == 0.35                                 # vector -> window plane
    assert out["pendant"]["brightness"] == 0.1                                      # aim: down, untouched


def test_accent_picks_nearest(room):
    out = render(room, _spec("accent"))["fixtures"]
    assert out["tv-right"]["brightness"] == 0.8
    assert out["tv-left"]["brightness"] == 0.2


def test_gradient_blends_in_oklab_not_hsv(room):
    """Halfway between blue and orange in OKLab is a low-saturation neutral, not a sweep through green."""
    spec = {"name": "agent.g", "layers": [{"op": "gradient", "from": "window", "to": [5, 2.1, 1.5],
            "colors": [{"hue_deg": 220, "sat": 0.9}, {"hue_deg": 30, "sat": 0.9}], "brightness": 0.5}]}
    mid = render(room, spec)["fixtures"]["pendant"]       # x = 2.5: halfway
    assert mid["sat"] < 0.3


def test_kelvin_rgb_is_warm_to_cool():
    warm, cool = scene_render.kelvin_rgb(2200), scene_render.kelvin_rgb(6500)
    assert warm[0] == 1.0 and warm[2] < cool[2]
