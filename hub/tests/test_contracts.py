# =============================================================================
# contracts/ is the source of truth (BUILD_ORDER §5). Every schema must be a
# valid JSON Schema, every ok-* example must validate and every bad-* example
# must be rejected. Takes are JSONL: line 1 = take-header, the rest take-event.
# =============================================================================

import glob
import json
import os

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "contracts")
SCHEMA_FILES = sorted(glob.glob(os.path.join(CONTRACTS, "*.schema.json")))

EXAMPLE_DIRS = {
    "intent": "intent", "state": "state", "ipc-request": "ipc-request", "ipc-response": "ipc-response",
    "ipc-event": "ipc-event", "fixtures": "fixtures", "console": "console",
    "board-packet": "board-packet", "board-frame": "board-frame",
    "room": "room", "scene-spec": "scene-spec",
}

# BUILD_ORDER §5.1 op table (+ the approved additions in DECISIONS.md X1, + agent.* for the Agent tab, + spaces.*).
SPEC_OPS = {
    "intent", "get_state", "subscribe", "refresh", "set_art_mode",
    "fixtures.list", "fixtures.discover", "fixtures.upsert", "fixtures.remove", "fixtures.test",
    "groups.list", "groups.upsert", "groups.remove", "banks.get", "banks.set",
    "takes.list", "takes.get", "takes.delete", "takes.rename", "takes.export", "takes.set_flag",
    "takes.marker.add", "takes.marker.remove", "rec.arm", "rec.start", "rec.stop",
    "play.start", "play.stop", "play.seek", "take.nudge", "board.pairing",
    "takes.duplicate", "cues.rename", "groups.set_limits", "banks.swap",
    "agent.ask", "agent.undo", "agent.save", "spaces.upsert", "spaces.remove",
    "scenes.list", "scenes.recall",
}
SPEC_INTENTS = {"fader", "flash", "assign", "bank", "fader_fn", "hue", "sat", "palette_apply",
                "palette_store", "cue_recall", "cue_store", "blackout", "who"}


def load(path):
    with open(path) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def registry():
    reg = Registry()
    for path in SCHEMA_FILES:
        reg = reg.with_resource(os.path.basename(path), Resource.from_contents(load(path)))
    return reg


def validator(name, registry):
    return Draft202012Validator(load(os.path.join(CONTRACTS, f"{name}.schema.json")), registry=registry)


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=os.path.basename)
def test_schema_is_valid(path):
    Draft202012Validator.check_schema(load(path))


def _examples(kind):
    return sorted(glob.glob(os.path.join(CONTRACTS, "examples", kind, "*.json")))


ALL_EXAMPLES = [(k, p) for k in EXAMPLE_DIRS for p in _examples(k)]


@pytest.mark.parametrize("kind,path", ALL_EXAMPLES, ids=lambda x: os.path.basename(x) if "/" in str(x) else x)
def test_example(kind, path, registry):
    v = validator(EXAMPLE_DIRS[kind], registry)
    errors = list(v.iter_errors(load(path)))
    if os.path.basename(path).startswith("ok-"):
        assert not errors, f"{path}: {errors[0].message if errors else ''}"
    else:
        assert errors, f"{path} should be rejected"


@pytest.mark.parametrize("kind", list(EXAMPLE_DIRS) + ["take"])
def test_every_contract_has_ok_and_bad_examples(kind):
    names = [os.path.basename(p) for p in glob.glob(os.path.join(CONTRACTS, "examples", kind, "*"))]
    assert any(n.startswith("ok-") for n in names) and any(n.startswith("bad-") for n in names), kind


def _take_errors(path, registry):
    header_v, event_v = validator("take-header", registry), validator("take-event", registry)
    with open(path) as f:
        lines = [json.loads(l) for l in f if l.strip()]
    errs = list(header_v.iter_errors(lines[0]))
    for ev in lines[1:]:
        errs += list(event_v.iter_errors(ev))
    return errs


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(CONTRACTS, "examples", "take", "*.jsonl"))),
                         ids=os.path.basename)
def test_take_examples(path, registry):
    errs = _take_errors(path, registry)
    if os.path.basename(path).startswith("ok-"):
        assert not errs, errs[0].message if errs else ""
    else:
        assert errs


def test_ipc_ops_match_spec():
    schema = load(os.path.join(CONTRACTS, "ipc-request.schema.json"))
    ops = {branch["properties"]["op"]["const"] for branch in schema["oneOf"]}
    assert ops == SPEC_OPS


def test_intents_match_spec():
    schema = load(os.path.join(CONTRACTS, "intent.schema.json"))
    names = {d["properties"]["intent"]["const"] for d in schema["$defs"].values() if "properties" in d}
    assert names == SPEC_INTENTS


def test_flash_rate_can_never_exceed_3hz():
    group = load(os.path.join(CONTRACTS, "fixtures.schema.json"))["$defs"]["group"]
    assert group["properties"]["max_flash_hz"]["maximum"] == 3
