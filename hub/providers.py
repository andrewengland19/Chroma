# =============================================================================
# hub/providers.py
# The source-agnostic now-playing seam. Pass 2 refactors the real engine onto
# the NowPlayingProvider Protocol; today only AppleTVProvider (pyatv) exists.
#
# The engine's existing NowPlaying shape (title/artist/album + track_id) is kept
# so downstream code — extract_colors → compute_brightness_scales → rgb_to_hsbk
# — is untouched. Artwork differs: the ATV hands us bytes directly (no URL), so
# artwork is a provider method rather than a field.
# =============================================================================

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import pyatv
from pyatv.const import Protocol as ATVProtocol, DeviceState

from paths import CHROMA_DIR

# Protocols we pair. AirPlay carries the tunneled MRP that exposes now-playing
# + artwork on tvOS 15+; Companion carries remote control / transport.
PAIR_PROTOCOLS = (ATVProtocol.AirPlay, ATVProtocol.Companion)

CREDENTIALS_PATH = os.path.join(CHROMA_DIR, "atv_credentials.json")

# Cocoa reference date (2001-01-01 UTC) as a Unix timestamp — MRP timestamps use it.
COCOA_EPOCH = 978307200.0


# =============================================================================
# NowPlaying — same shape the engine already uses
# =============================================================================

@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    album: str = ""
    playing: bool = False
    hash: str = ""  # pyatv content hash — robust across identical title/artist
    # Position/duration in ms. pyatv reports whole seconds (truncated), so pos_ms is a
    # coarse sample; SongClock refines it with AppleTVProvider.raw_anchor().
    pos_ms: Optional[int] = None
    dur_ms: Optional[int] = None

    @classmethod
    def from_playing(cls, p) -> "NowPlaying":
        """Build from a pyatv Playing object (used by now_playing + push listener)."""
        pos = getattr(p, "position", None)
        total = getattr(p, "total_time", None)
        return cls(
            title=p.title or "",
            artist=p.artist or "",
            album=p.album or "",
            playing=(p.device_state == DeviceState.Playing),
            hash=getattr(p, "hash", "") or "",
            pos_ms=int(pos) * 1000 if pos is not None else None,
            dur_ms=int(total) * 1000 if total is not None else None,
        )

    def track_id(self) -> str:
        """Dedup key; prefers pyatv's content hash, falls back to artist|title."""
        return self.hash or f"{self.artist}|||{self.title}"

    def has_track(self) -> bool:
        return bool(self.title or self.artist)

    def label(self) -> str:
        return f"{self.artist} — {self.title}".strip(" —")


# =============================================================================
# Provider interface
# =============================================================================

@runtime_checkable
class NowPlayingProvider(Protocol):
    async def now_playing(self) -> Optional[NowPlaying]: ...
    async def artwork_bytes(self) -> Optional[bytes]: ...
    async def play_pause(self) -> None: ...
    async def next(self) -> None: ...
    async def previous(self) -> None: ...


# =============================================================================
# Credential persistence
# =============================================================================

def save_credentials(identifier: str, name: str, credentials: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(CREDENTIALS_PATH), exist_ok=True)
    tmp = CREDENTIALS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(
            {"identifier": identifier, "name": name, "credentials": credentials},
            f,
            indent=2,
        )
    os.replace(tmp, CREDENTIALS_PATH)  # atomic


def load_credentials() -> Optional[dict]:
    if not os.path.exists(CREDENTIALS_PATH):
        return None
    try:
        with open(CREDENTIALS_PATH) as f:
            return json.load(f)
    except Exception:
        return None


# =============================================================================
# AppleTVProvider — pyatv-backed. Async; the sync facade is a Pass 2 concern.
# =============================================================================

class AppleTVProvider:
    """Connects to a paired Apple TV and exposes now-playing + artwork + transport.

    Usage:
        provider = await AppleTVProvider.connect(loop)
        np = await provider.now_playing()
        art = await provider.artwork_bytes()
        await provider.close()
    """

    def __init__(self, atv, config):
        self._atv = atv
        self._config = config
        self.name = config.name

    @classmethod
    async def connect(cls, loop, stored: Optional[dict] = None) -> "AppleTVProvider":
        stored = stored or load_credentials()
        if not stored:
            raise RuntimeError(
                "No stored Apple TV credentials. Run `python hub/pair_atv.py` first."
            )

        results = await pyatv.scan(loop, identifier=stored["identifier"], timeout=5)
        if not results:
            raise RuntimeError(
                f"Apple TV '{stored.get('name')}' ({stored['identifier']}) not found on "
                "the LAN. Is it awake and on this network?"
            )
        config = results[0]

        # Re-attach stored credentials per protocol.
        for proto_name, creds in stored["credentials"].items():
            config.set_credentials(ATVProtocol[proto_name], creds)

        atv = await pyatv.connect(config, loop)
        return cls(atv, config)

    async def now_playing(self) -> Optional[NowPlaying]:
        try:
            p = await self._atv.metadata.playing()
        except Exception:
            return None
        if p is None:
            return None
        return NowPlaying.from_playing(p)

    async def artwork_bytes(self, width: int = 600, height: int = 600) -> Optional[bytes]:
        try:
            art = await self._atv.metadata.artwork(width=width, height=height)
        except Exception:
            return None
        return art.bytes if art else None

    # --- transport (used from Pass 3's control plane; handy in the spike too) ---
    async def play_pause(self) -> None:
        await self._atv.remote_control.play_pause()

    async def next(self) -> None:
        await self._atv.remote_control.next()

    async def previous(self) -> None:
        await self._atv.remote_control.previous()

    async def seek(self, pos_ms: int) -> None:
        """Takes timeline scrub (M5). pyatv positions are whole seconds."""
        await self._atv.remote_control.set_position(int(round(pos_ms / 1000.0)))

    def raw_anchor(self) -> Optional[dict]:
        """Precise position anchor from MRP: {elapsed_s, anchor_wall, rate}.

        pyatv's public Playing.position truncates to whole seconds; the MRP player
        state underneath keeps the float elapsedTime, the (Cocoa) wall time it was
        sampled and the playback rate. This reads that private state — guarded, so
        any pyatv change just yields None and SongClock falls back to the coarse path.
        """
        try:
            meta = self._atv.metadata
            main = getattr(meta, "main_instance", None)
            candidates = ([main] if main is not None else []) + [
                i for i in (getattr(meta, "instances", None) or []) if i is not main]
            for inst in candidates:
                psm = getattr(inst, "psm", None)
                if psm is None:
                    continue
                st = psm.playing
                elapsed = st.metadata_field("elapsedTime")
                stamp = st.metadata_field("elapsedTimeTimestamp")
                if elapsed is None or not stamp:
                    continue
                rate = st.metadata_field("playbackRate") or 0.0
                return {"elapsed_s": float(elapsed), "anchor_wall": float(stamp) + COCOA_EPOCH,
                        "rate": float(rate)}
        except Exception:
            return None
        return None

    @property
    def push_updater(self):
        return self._atv.push_updater

    def set_device_listener(self, listener) -> None:
        """Register a pyatv DeviceListener (connection_lost / connection_closed)."""
        self._atv.listener = listener

    def start_push(self, listener) -> None:
        self._atv.push_updater.listener = listener
        self._atv.push_updater.start()

    def stop_push(self) -> None:
        try:
            self._atv.push_updater.stop()
        except Exception:
            pass

    async def close(self) -> None:
        self.stop_push()
        self._atv.close()
