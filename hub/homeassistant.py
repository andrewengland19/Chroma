# =============================================================================
# hub/homeassistant.py — Home Assistant connector (STUB: built and tested against a
# fake HA, never exercised against a real one yet — docs/mvp/HOMEASSISTANT.md, HA.1).
#
# REST for commands and health, WebSocket for state_changed events, long-lived access
# token. DISABLED BY DEFAULT: while `ha.enabled` is false, no method opens a socket or
# sends a byte (calls raise HaDisabled; health() reports "disabled").
#
# Config lives in ~/.chroma/config.json under "ha" (see HaConfig); the token never does:
# keystore.load_ha_token() (env CHROMA_HA_TOKEN, else Keychain chroma-hub/homeassistant).
#
# Safety (BUILD_ORDER §8): only `light.*` entities are ever commanded; brightness is
# capped by `max_brightness`; any error means NOTHING is sent (never a retry at full,
# never a "recovery" turn_on); a failed or bad-token connection is reported, not
# papered over. The 3 Hz strobe cap is enforced per entity in outputs/ha.py.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from keystore import load_ha_token

log = logging.getLogger("chroma-hub")

DEFAULT_URL = "http://homeassistant.local:8123"
LIGHT_DOMAIN = "light."


class HaError(Exception):
    """Base class; messages never contain the token."""


class HaDisabled(HaError):
    """ha.enabled is false: nothing was sent."""


class HaAuthError(HaError):
    """No token, or Home Assistant rejected it (401 / auth_invalid)."""


class HaUnavailable(HaError):
    """Could not reach Home Assistant, or it answered with an error."""


class HaConfigError(ValueError):
    """Bad `ha` block in config.json."""


@dataclass
class HaEntity:
    fixture: str                          # Chroma fixture id this entity stands in for
    transition_ms: Optional[int] = None   # None = HaConfig.default_transition_ms


@dataclass
class HaConfig:
    enabled: bool = False
    url: str = DEFAULT_URL
    entities: dict = field(default_factory=dict)   # entity_id -> HaEntity
    default_transition_ms: int = 1000
    max_brightness: float = 1.0                    # cap applied to every command
    min_interval_ms: int = 340                     # per entity: <= 3 Hz on the wire
    timeout_s: float = 5.0

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "HaConfig":
        d = d or {}
        if not isinstance(d, dict):
            raise HaConfigError("ha must be an object")
        ents = {}
        for eid, v in (d.get("entities") or {}).items():
            if not isinstance(eid, str) or not eid.startswith(LIGHT_DOMAIN):
                raise HaConfigError(f"ha.entities: {eid!r} is not a light.* entity")
            if isinstance(v, str):
                v = {"fixture": v}
            if not isinstance(v, dict) or not isinstance(v.get("fixture"), str) or not v["fixture"]:
                raise HaConfigError(f"ha.entities.{eid} needs a fixture id")
            tm = v.get("transition_ms")
            if tm is not None and (isinstance(tm, bool) or not isinstance(tm, int) or not 0 <= tm <= 60000):
                raise HaConfigError(f"ha.entities.{eid}.transition_ms must be 0..60000")
            ents[eid] = HaEntity(v["fixture"], tm)
        cfg = cls(
            enabled=d.get("enabled") is True,
            url=str(d.get("url") or DEFAULT_URL).strip(),
            entities=ents,
            default_transition_ms=_num(d, "default_transition_ms", 1000, 0, 60000, int),
            max_brightness=_num(d, "max_brightness", 1.0, 0.0, 1.0, float),
            min_interval_ms=max(340, _num(d, "min_interval_ms", 340, 0, 60000, int)),  # never above 3 Hz
            timeout_s=_num(d, "timeout_s", 5.0, 0.5, 60.0, float),
        )
        parts = urlsplit(cfg.url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise HaConfigError(f"ha.url must be http(s)://host[:port] (got {cfg.url!r})")
        cfg.url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        return cfg

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "url": self.url,
                "entities": {e: {"fixture": v.fixture, "transition_ms": v.transition_ms}
                             for e, v in self.entities.items()},
                "default_transition_ms": self.default_transition_ms,
                "max_brightness": self.max_brightness,
                "min_interval_ms": self.min_interval_ms, "timeout_s": self.timeout_s}

    def transition_for(self, entity_id: str) -> int:
        e = self.entities.get(entity_id)
        return e.transition_ms if e and e.transition_ms is not None else self.default_transition_ms

    def entity_for_fixture(self, fixture_id: str) -> Optional[str]:
        for eid, e in self.entities.items():
            if e.fixture == fixture_id:
                return eid
        return None

    @property
    def ws_url(self) -> str:
        p = urlsplit(self.url)
        return urlunsplit(("wss" if p.scheme == "https" else "ws", p.netloc,
                           p.path + "/api/websocket", "", ""))


def _num(d: dict, key: str, default, lo, hi, typ):
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise HaConfigError(f"ha.{key} must be a number between {lo} and {hi}")
    return typ(v)


StateCallback = Callable[[str, Optional[dict], Optional[dict]], None]


class HaClient:
    def __init__(self, cfg: HaConfig, token: Optional[str] = None,
                 token_loader: Callable[[], Optional[str]] = load_ha_token,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep):
        self.cfg = cfg
        self._token = token
        self._token_loader = token_loader
        self._sleep = sleep
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self.connected = asyncio.Event()      # set while the WebSocket is subscribed
        self.reconnects = 0
        self.auth_failed = False

    # ---- plumbing ----------------------------------------------------------------
    def _require_enabled(self) -> None:
        if not self.cfg.enabled:
            raise HaDisabled("Home Assistant connector is disabled (ha.enabled is false)")

    def _get_token(self) -> str:
        if not self._token:
            self._token = self._token_loader()
        if not self._token:
            raise HaAuthError("no Home Assistant token (store one: see docs/mvp/HOMEASSISTANT.md)")
        return self._token

    def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=self.cfg.timeout_s))
        return self._session

    async def _request(self, method: str, path: str, body: Optional[dict] = None):
        self._require_enabled()
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        try:
            async with self._sess().request(method, self.cfg.url + path, json=body, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=self.cfg.timeout_s)) as r:
                if r.status == 401:
                    self.auth_failed = True
                    raise HaAuthError("Home Assistant rejected the token (401)")
                if r.status >= 400:
                    raise HaUnavailable(f"Home Assistant answered {r.status} for {method} {path}")
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as e:   # ValueError: non-JSON body
            raise HaUnavailable(f"cannot reach Home Assistant at {self.cfg.url}: {type(e).__name__}") from None

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            await asyncio.gather(self._ws_task, return_exceptions=True)
            self._ws_task = None
        self.connected.clear()
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- health / listing --------------------------------------------------------
    async def health(self) -> dict:
        """Never raises. state: disabled | no_token | auth_failed | unreachable | ok."""
        if not self.cfg.enabled:
            return {"ok": False, "state": "disabled"}
        try:
            info = await self._request("GET", "/api/config")
        except HaAuthError as e:
            return {"ok": False, "state": "no_token" if not self._token else "auth_failed", "error": str(e)}
        except HaUnavailable as e:
            return {"ok": False, "state": "unreachable", "error": str(e)}
        return {"ok": True, "state": "ok", "version": (info or {}).get("version"),
                "location": (info or {}).get("location_name")}

    async def list_lights(self) -> list:
        states = await self._request("GET", "/api/states")
        out = []
        for s in states or []:
            eid = s.get("entity_id", "")
            if not eid.startswith(LIGHT_DOMAIN):
                continue
            a = s.get("attributes") or {}
            out.append({"entity_id": eid, "name": a.get("friendly_name", eid), "state": s.get("state"),
                        "color_modes": a.get("supported_color_modes", []),
                        "mapped_to": self.cfg.entities[eid].fixture if eid in self.cfg.entities else None})
        return sorted(out, key=lambda e: e["entity_id"])

    # ---- commands ----------------------------------------------------------------
    def _check_entity(self, entity_id: str) -> None:
        if not isinstance(entity_id, str) or not entity_id.startswith(LIGHT_DOMAIN):
            raise HaError(f"refusing to command {entity_id!r}: only light.* entities")

    async def turn_on(self, entity_id: str, brightness: Optional[float] = None,
                      hs: Optional[tuple] = None, transition_ms: Optional[int] = None) -> None:
        """brightness 0..1 (capped by max_brightness; <= 0 means turn_off); hs = (hue deg, sat 0..1)."""
        self._require_enabled()
        self._check_entity(entity_id)
        tms = self.cfg.transition_for(entity_id) if transition_ms is None else int(transition_ms)
        if brightness is not None and brightness <= 0:
            return await self.turn_off(entity_id, tms)
        data: dict = {"entity_id": entity_id, "transition": round(max(0, tms) / 1000.0, 2)}
        if brightness is not None:
            b = min(float(brightness), self.cfg.max_brightness)
            data["brightness"] = max(1, min(255, round(b * 255)))
        elif self.cfg.max_brightness < 1.0:
            # a bare turn_on would restore HA's remembered level, which could exceed the cap
            data["brightness"] = max(1, round(self.cfg.max_brightness * 255))
        if hs is not None:
            data["hs_color"] = [round(float(hs[0]) % 360.0, 1), round(min(1.0, max(0.0, float(hs[1]))) * 100, 1)]
        await self._request("POST", "/api/services/light/turn_on", data)

    async def turn_off(self, entity_id: str, transition_ms: Optional[int] = None) -> None:
        self._require_enabled()
        self._check_entity(entity_id)
        tms = self.cfg.transition_for(entity_id) if transition_ms is None else int(transition_ms)
        await self._request("POST", "/api/services/light/turn_off",
                            {"entity_id": entity_id, "transition": round(max(0, tms) / 1000.0, 2)})

    # ---- state_changed subscription ----------------------------------------------
    def subscribe_state_changes(self, callback: StateCallback) -> asyncio.Task:
        """Start (once) a background task that keeps a WebSocket subscribed to state_changed,
        calling callback(entity_id, old_state, new_state) for light.* entities. Reconnects with
        backoff (1, 2, 4 … 30 s); stops for good on a rejected token."""
        self._require_enabled()
        if self._ws_task and not self._ws_task.done():
            raise HaError("already subscribed")
        self._ws_task = asyncio.get_running_loop().create_task(self._ws_loop(callback))
        return self._ws_task

    async def _ws_loop(self, callback: StateCallback) -> None:
        delay = 1.0
        first = True
        while True:
            try:
                await self._ws_session(callback, announce=not first)
                delay = 1.0            # a session that got as far as subscribing resets the backoff
            except asyncio.CancelledError:
                raise
            except HaAuthError as e:
                self.auth_failed = True
                log.warning(f"Home Assistant: {e}; giving up until restart")
                return
            except Exception as e:
                log.info(f"Home Assistant WebSocket down ({type(e).__name__}); retry in {delay:.0f}s")
            finally:
                self.connected.clear()
            first = False
            await self._sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _ws_session(self, callback: StateCallback, announce: bool) -> None:
        token = self._get_token()
        async with self._sess().ws_connect(self.cfg.ws_url, heartbeat=30,
                                         timeout=aiohttp.ClientWSTimeout(ws_close=self.cfg.timeout_s)) as ws:
            rj = lambda: ws.receive_json(timeout=self.cfg.timeout_s)   # noqa: E731
            msg = await rj()
            if msg.get("type") != "auth_required":
                raise HaUnavailable("unexpected WebSocket greeting")
            await ws.send_json({"type": "auth", "access_token": token})
            msg = await rj()
            if msg.get("type") == "auth_invalid":
                raise HaAuthError("Home Assistant rejected the token (auth_invalid)")
            if msg.get("type") != "auth_ok":
                raise HaUnavailable("unexpected WebSocket auth reply")
            await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
            res = await rj()
            if res.get("type") != "result" or not res.get("success"):
                raise HaUnavailable("subscribe_events refused")
            if announce:
                self.reconnects += 1
            self.connected.set()
            async for m in ws:
                if m.type != aiohttp.WSMsgType.TEXT:
                    break
                ev = m.json()
                if ev.get("type") != "event":
                    continue
                d = (ev.get("event") or {}).get("data") or {}
                eid = d.get("entity_id", "")
                if eid.startswith(LIGHT_DOMAIN):
                    try:
                        callback(eid, d.get("old_state"), d.get("new_state"))
                    except Exception as e:     # a bad listener must not drop the connection
                        log.warning(f"Home Assistant state callback failed: {e}")
