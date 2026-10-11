# =============================================================================
# hub/tests/conftest.py — sandbox every test away from the user's real system.
#
# HOME is pointed at a throwaway directory BEFORE any hub module is imported, so
# every `~/.chroma/...` constant (config, layout, scenes, state, artwork) and
# `~/.lifx_cli_cache.json` resolve inside it. On top of that, autouse fixtures
# make real devices unreachable: lifxlan discovery finds nothing, constructing a
# real lifxlan.Light fails, pyatv scan/connect fail, and the Keychain is never
# consulted. A test that needs bulbs or an Apple TV uses hub/testing fakes.
# =============================================================================

import asyncio
import os
import shutil
import sys
import tempfile

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HUB)

_TEST_HOME = tempfile.mkdtemp(prefix="chroma-test-home-")
os.environ["HOME"] = _TEST_HOME
os.environ["CHROMA_HOME"] = os.path.join(_TEST_HOME, ".chroma")
os.environ.pop("ANTHROPIC_API_KEY", None)
for p in (REPO, HUB):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

CHROMA = os.path.join(_TEST_HOME, ".chroma")

import config as _config  # noqa: E402

assert _config.CONFIG_PATH.startswith(_TEST_HOME), "test HOME sandbox not applied"


class _NoRealDevices(RuntimeError):
    pass


def _refuse(*_a, **_k):
    raise _NoRealDevices("tests must not touch real devices — use hub/testing fakes")


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    """Fresh ~/.chroma per test; real LIFX / Apple TV / Keychain unreachable."""
    shutil.rmtree(CHROMA, ignore_errors=True)
    os.makedirs(CHROMA)

    import importlib
    import keystore
    monkeypatch.setattr(keystore, "load_anthropic_key", lambda: None)
    monkeypatch.setattr(keystore, "has_anthropic_key", lambda: False)
    for name in ("engine", "enhancers", "lights_agent"):
        mod = importlib.import_module(name)   # import now so later imports can't dodge the patch
        if hasattr(mod, "has_anthropic_key"):
            monkeypatch.setattr(mod, "has_anthropic_key", lambda: False)
        if hasattr(mod, "load_anthropic_key"):
            monkeypatch.setattr(mod, "load_anthropic_key", lambda: None)

    import lifxlan
    from testing.fake_lifx import FakeLifxLAN
    monkeypatch.setattr(lifxlan, "LifxLAN", lambda *a, **k: FakeLifxLAN([]))
    monkeypatch.setattr(lifxlan, "Light", _refuse)

    import pyatv
    monkeypatch.setattr(pyatv, "scan", _refuse)
    monkeypatch.setattr(pyatv, "connect", _refuse)
    monkeypatch.setattr(pyatv, "pair", _refuse)
    yield CHROMA


@pytest.fixture
def chroma_dir():
    return CHROMA


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


# ---- engine builder ---------------------------------------------------------

# Fixed room for golden tests: four corners + centre (normalised art coords).
GOLDEN_LAYOUT = {
    "TV Left": (0.25, 0.25),
    "TV Right": (0.75, 0.25),
    "Couch Left": (0.25, 0.75),
    "Right couch": (0.75, 0.75),
    "Washer": (0.5, 0.5),
}


def write_layout(positions: dict, region: float = 0.4) -> None:
    import json
    import layout
    data = {"region_w": region, "region_h": region,
            "lights": {k: {"x": x, "y": y} for k, (x, y) in positions.items()}}
    os.makedirs(os.path.dirname(layout.LAYOUT_PATH), exist_ok=True)
    with open(layout.LAYOUT_PATH, "w") as f:
        json.dump(data, f)


def write_config(**overrides) -> None:
    from config import Config
    cfg = Config()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    cfg.save()


@pytest.fixture
def make_engine():
    """async factory: (engine, bulbs, provider) with fake bulbs + FakeProvider."""
    async def _make(provider=None, bulbs=None, layout_positions=None, **config):
        import engine as E
        from testing.clock import FakeClock
        from testing.fake_lifx import install_fake_bulbs
        from testing.fake_provider import FakeProvider
        write_layout(GOLDEN_LAYOUT if layout_positions is None else layout_positions)
        write_config(**config)
        eng = E.Engine(asyncio.get_running_loop(), install_signals=False)
        bulbs = install_fake_bulbs(eng.lifx, bulbs)
        eng.provider = provider or FakeProvider(clock=FakeClock())
        return eng, bulbs, eng.provider
    return _make
