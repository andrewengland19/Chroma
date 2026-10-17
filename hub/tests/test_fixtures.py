# M3: fixtures / groups / banks model (hub/fixtures.py).

import json
import os

import pytest

from conftest import assert_valid
from fixtures import MAX_FLASH_HZ, FixtureStore, RigError
from testing.fake_lifx import BEDROOM, LIVING_ROOM

BULBS = ([{"label": l, "ip": ip, "mac": mac, "group": "Living Room"} for l, ip, mac in LIVING_ROOM]
         + [{"label": l, "ip": ip, "mac": mac, "group": "Bedroom"} for l, ip, mac in BEDROOM])


@pytest.fixture
def store(tmp_path):
    return FixtureStore.load(str(tmp_path / "fixtures.json"))


def test_default_rig_matches_the_mockup_and_the_schema(store, tmp_path):
    d = store.to_dict()
    assert_valid("fixtures", d)
    assert store.fresh and store.rev == 1
    assert store.banks["A"] == ["lr-all", "bed", "counter", "playbill", "lr-l", "lr-r", "accent", "fx"]
    assert store.banks["B"] == ["tv", "couch", "lamp", "pb-1-4", "pb-5-8", "pb-9-12", "br-1-2", "br-3-4"]
    assert store.banks["C"] == [None] * 8
    pb = store.groups["playbill"]
    assert (pb.max_level, pb.max_flash_hz, pb.idle_dim_min) == (0.6, 3.0, 20)
    assert len(pb.members) == 12 and store.groups["pb-5-8"].members == [f"playbill-0{i}" for i in range(5, 9)]
    assert store.fixtures["virtual-light-1"].status == "active"
    with open(tmp_path / "fixtures.json") as f:
        assert json.load(f) == d                      # written at seed time
    assert FixtureStore.load(str(tmp_path / "fixtures.json")).fresh is False


def test_import_maps_real_bulbs_onto_the_mockup_groups(store):
    pos = {"TV Left": (0.3, 0.25)}
    res = store.import_discovered(BULBS, positions=lambda l: pos.get(l, (0.5, 0.5)))
    assert res["found"] == 8 and len(res["added"]) == 8 and res["updated"] == []
    f = {x.label: x for x in store.fixtures.values()}
    groups = lambda label: sorted(g.id for g in store.groups_of(f[label].id))   # noqa: E731
    assert groups("TV Left") == ["lr-all", "lr-l", "tv"]
    assert groups("TV Right") == ["lr-all", "lr-r", "tv"]
    assert groups("Couch Left") == ["couch", "lr-all", "lr-l"]
    assert groups("Right couch") == ["couch", "lr-all", "lr-r"]
    assert groups("Washer") == ["accent", "lamp", "lr-all"]        # the mockup's "Lamp" (D5)
    assert f["Bright Light"].status == "not_added" and groups("Bright Light") == ["bed"]
    assert (f["TV Left"].x, f["TV Left"].y) == (0.3, 0.25)
    assert [x.label for x in store.active("lifx")] == [l for l, _, _ in LIVING_ROOM]
    assert_valid("fixtures", store.to_dict())
    # again, with one bulb on a new IP: no duplicates, IP refreshed
    moved = [dict(b) for b in BULBS]
    moved[0]["ip"] = "192.168.0.99"
    res = store.import_discovered(moved + [{"label": "junk", "ip": "x", "mac": "y"}])
    assert res["added"] == [] and res["updated"] == ["tv-left"]
    assert store.fixtures["tv-left"].ip == "192.168.0.99"


def test_edits_persist_and_bump_rev(store, tmp_path):
    store.import_discovered(BULBS)
    rev = store.rev
    fx = store.upsert_fixture({"label": "Desk lamp", "type": "virtual", "status": "active", "class": "fast",
                               "x": 0.2, "y": 0.9})
    assert fx.id == "desk-lamp"
    store.upsert_group({"id": "desk", "name": "DESK", "color": "#ffaa00", "members": ["desk-lamp", "tv-left"],
                        "max_level": 0.5, "max_flash_hz": 2, "idle_dim_min": None})
    store.set_bank("C", 1, "desk")
    assert store.rev == rev + 3
    again = FixtureStore.load(str(tmp_path / "fixtures.json"))
    assert again.to_dict() == store.to_dict()
    assert again.banks["C"][0] == "desk" and again.groups["desk"].max_flash_hz == 2
    store.remove_group("desk")
    assert store.banks["C"][0] is None
    store.remove_fixture("tv-left")
    assert all("tv-left" not in g.members for g in store.groups.values())


@pytest.mark.parametrize("group,msg", [
    ({"max_flash_hz": 5}, "at most 3 Hz"),
    ({"max_flash_hz": 0}, "above 0"),
    ({"max_level": 1.2}, "max level"),
    ({"name": "THIS NAME IS FAR TOO LONG"}, "1–16"),
    ({"id": "Bad Id"}, "lowercase"),
    ({"members": ["ghost"]}, "unknown fixture"),
    ({"idle_dim_min": 0}, "idle auto-dim"),
])
def test_group_validation(store, group, msg):
    g = {"id": "x", "name": "X", "color": "#112233", "members": [], "max_level": 1.0,
         "max_flash_hz": 3, "idle_dim_min": None, **group}
    with pytest.raises(RigError, match=msg):
        store.upsert_group(g)


@pytest.mark.parametrize("fx,msg", [
    ({"type": "wled", "status": "active", "class": "fast"}, "phase 2"),
    ({"type": "lifx", "status": "active", "class": "slow"}, "IP address"),
    ({"type": "virtual", "status": "active", "class": "fast", "x": 2}, "between 0 and 1"),
    ({"type": "lamp", "status": "active", "class": "fast"}, "lifx, virtual or wled"),
    ({"type": "lifx", "status": "active", "class": "slow", "ip": "1.2.3.4", "mac": "zz"}, "MAC"),
])
def test_fixture_validation(store, fx, msg):
    with pytest.raises(RigError, match=msg):
        store.upsert_fixture({"label": "X", **fx})


def test_duplicate_mac_and_bad_bank_edits(store):
    store.import_discovered(BULBS)
    with pytest.raises(RigError, match="already uses MAC"):
        store.upsert_fixture({"label": "Clone", "type": "lifx", "status": "active", "class": "slow",
                              "ip": "192.168.0.70", "mac": LIVING_ROOM[0][2]})
    with pytest.raises(RigError, match="bank"):
        store.set_bank("D", 1, "fx")
    with pytest.raises(RigError, match="channel"):
        store.set_bank("A", 9, "fx")
    with pytest.raises(RigError, match="no group"):
        store.set_bank("A", 1, "ghost")


def test_ip_only_lifx_fixture_is_allowed(store):
    fx = store.upsert_fixture({"label": "Garage", "type": "lifx", "status": "active", "class": "slow",
                               "ip": "192.168.0.80"})
    assert fx.mac is None and fx.to_dict()["ip"] == "192.168.0.80"


def test_limits_are_the_most_restrictive_of_all_groups(store):
    store.import_discovered(BULBS)
    assert store.limits("tv-left") == (1.0, MAX_FLASH_HZ)
    store.upsert_group({**store.groups["tv"].to_dict(), "max_level": 0.4, "max_flash_hz": 1.5})
    store.upsert_group({**store.groups["lr-l"].to_dict(), "max_level": 0.7, "max_flash_hz": 2.5})
    assert store.limits("tv-left") == (0.4, 1.5)
    assert store.limits("right-couch") == (1.0, 3.0)
    assert store.limits("playbill-01") == (0.6, 3.0)
    assert store.limits("nobody") == (1.0, 3.0)


def test_members_of_returns_active_fixtures_only(store):
    store.import_discovered(BULBS)
    assert [f.id for f in store.members_of("lr-all")] == ["tv-left", "tv-right", "couch-left", "right-couch", "washer"]
    assert store.members_of("bed") == [] and [f.id for f in store.members_of("bed", active_only=False)]
    assert store.members_of("playbill") == []                 # planned WLED: nothing to drive yet


def test_rediscovery_keeps_the_users_name(store):
    store.import_discovered(BULBS)
    store.upsert_fixture({**store.fixtures["right-couch"].to_dict(), "label": "Couch R"})
    store.import_discovered(BULBS)
    assert store.fixtures["right-couch"].label == "Couch R"


def test_corrupt_file_is_kept_aside_and_the_default_rig_used(tmp_path):
    p = tmp_path / "fixtures.json"
    p.write_text('{"v": 1, "rev": 3, "fixtures": [ {"id": "BAD ID"')
    s = FixtureStore.load(str(p))
    assert s.recovered and os.path.exists(s.recovered) and s.fresh
    assert_valid("fixtures", s.to_dict())
    p.write_text(json.dumps({"v": 1, "rev": 1, "fixtures": [], "banks": {},
                             "groups": [{"id": "x", "name": "X", "color": "#112233", "max_flash_hz": 9}]}))
    s = FixtureStore.load(str(p))
    assert s.recovered and "playbill" in s.groups            # invalid limits are never loaded


def test_set_limits_and_swap(store):
    store.import_discovered(BULBS)
    members = list(store.groups["lr-all"].members)
    g = store.set_limits("lr-all", max_level=0.5)
    assert g.max_level == 0.5 and g.members == members and g.max_flash_hz == 3.0
    with pytest.raises(RigError, match="at most 3 Hz"):
        store.set_limits("lr-all", max_flash_hz=3.5)
    with pytest.raises(RigError, match="no group"):
        store.set_limits("ghost", max_level=0.1)
    rev = store.rev
    store.swap_banks(("A", 1), ("C", 8))
    assert store.banks["A"][0] is None and store.banks["C"][7] == "lr-all" and store.rev == rev + 1
    with pytest.raises(RigError, match="cells"):
        store.swap_banks(("A", 0), ("A", 1))


def test_spaces_are_seeded_from_the_lifx_groups(store):
    assert list(store.spaces) == ["living-room"]
    assert {f.space for f in store.fixtures.values()} == {"living-room"}      # planned + virtual
    store.import_discovered(BULBS)
    assert list(store.spaces) == ["living-room", "bedroom"]
    assert store.spaces["bedroom"].name == "Bedroom"
    f = {x.label: x for x in store.fixtures.values()}
    assert f["TV Left"].space == "living-room" and f["Bright Light"].space == "bedroom"
    assert [x.label for x in store.in_spaces(["bedroom"])] == [l for l, _, _ in BEDROOM]
    assert_valid("fixtures", store.to_dict())


def test_a_file_from_before_spaces_is_migrated(tmp_path, store):
    store.import_discovered(BULBS)
    d = store.to_dict()
    del d["spaces"]
    for fd in d["fixtures"]:
        fd.pop("space", None)
    p = tmp_path / "old.json"
    p.write_text(json.dumps(d))
    s = FixtureStore.load(str(p))
    assert list(s.spaces) == ["living-room", "bedroom"]
    assert s.fixtures["tv-left"].space == "living-room" and s.fixtures["bright-light"].space == "bedroom"
    assert s.fixtures["virtual-light-1"].space == "living-room"
    assert json.loads(p.read_text())["spaces"][1] == {"id": "bedroom", "name": "Bedroom"}   # written back
    assert s.rev == d["rev"]


def test_space_edits(store):
    store.import_discovered(BULBS)
    sp = store.upsert_space({"name": "Kitchen"})
    assert sp.id == "kitchen" and store.spaces["kitchen"].name == "Kitchen"
    store.upsert_space({"id": "bedroom", "name": "Main bedroom"})
    assert store.spaces["bedroom"].name == "Main bedroom"
    with pytest.raises(RigError, match="already a space"):
        store.upsert_space({"name": "kitchen"})
    with pytest.raises(RigError, match="1–24"):
        store.upsert_space({"name": " "})
    fx = store.fixtures["washer"].to_dict()
    store.upsert_fixture({**fx, "space": "kitchen"})
    assert store.fixtures["washer"].space == "kitchen"
    with pytest.raises(RigError, match="no space"):
        store.upsert_fixture({**fx, "space": "garage"})
    store.upsert_fixture({k: v for k, v in fx.items() if k != "space"})     # an old UI keeps the Space
    assert store.fixtures["washer"].space == "kitchen"
    store.remove_space("kitchen")
    assert "kitchen" not in store.spaces and store.fixtures["washer"].space == "living-room"
    with pytest.raises(RigError, match="default space"):
        store.remove_space("living-room")
    assert_valid("fixtures", store.to_dict())
