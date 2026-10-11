# =============================================================================
# hub/testing/fake_provider.py — a scripted stand-in for AppleTVProvider.
#
# Plays a list of tracks against an injectable clock: position advances while
# playing, freezes on pause, jumps on seek, and moves to the next track at the
# end (or on next()/previous()). Exposes the same surface the Engine uses on
# AppleTVProvider (now_playing, artwork_bytes, transport, push + device
# listeners, close) and hands the push listener duck-typed pyatv `Playing`
# objects, so Engine code runs unmodified.
#
# Position is reported the way pyatv 0.18 does it: `position`/`total_time` are
# whole seconds (truncated). The precise anchor (float elapsed seconds + the
# wall time it was sampled, like MRP's elapsedTime/elapsedTimeTimestamp) is
# available from elapsed_anchor() for SongClock tests.
# =============================================================================

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from pyatv.const import DeviceState

from .art import LEVITATING, MIDNIGHT, OLDER, make_art
from .clock import RealClock


@dataclass
class FakeTrack:
    title: str
    artist: str
    album: str
    dur_ms: int
    art: Optional[bytes] = None
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            key = f"{self.artist}|{self.album}|{self.title}".encode()
            self.hash = hashlib.sha1(key).hexdigest()[:16]


@dataclass
class FakePlaying:
    """Duck-typed pyatv.interface.Playing (only the fields the hub reads)."""
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    device_state: DeviceState
    hash: str
    position: Optional[int]
    total_time: Optional[int]
    extra: dict = field(default_factory=dict)


def default_tracks() -> list:
    """The `chromad --fake` playlist (titles from the mockups)."""
    return [
        FakeTrack("Older", "Sasha Alex Sloan", "Loser - EP", 205_000, make_art(OLDER)),
        FakeTrack("Levitating (Remix)", "Dua Lipa", "Club Future Nostalgia", 242_000,
                  make_art(LEVITATING)),
        FakeTrack("Midnight City", "M83", "Hurry Up, We're Dreaming", 243_000, make_art(MIDNIGHT)),
    ]


class FakeProvider:
    name = "Fake Apple TV"

    def __init__(self, tracks: Optional[list] = None, clock=None, script: Optional[list] = None,
                 start_playing: bool = True, auto_advance: bool = True):
        self.clock = clock or RealClock()
        self.tracks = list(tracks) if tracks is not None else default_tracks()
        self.index = 0
        self.auto_advance = auto_advance
        self._playing = bool(start_playing and self.tracks)
        self._anchor_pos_ms = 0.0
        self._anchor_mono = self.clock.monotonic()
        self._t0 = self.clock.monotonic()
        # script: [(t_seconds_from_start, action, *args)], applied by tick()
        self._script = sorted(script or [], key=lambda s: s[0])
        self._push_listener = None
        self._device_listener = None
        self.closed = False
        self.calls: list = []   # transport calls, for assertions

    # ---- position ----------------------------------------------------------
    @property
    def track(self) -> Optional[FakeTrack]:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    @property
    def is_playing(self) -> bool:
        return self._playing and self.track is not None

    def position_ms(self) -> float:
        if self.track is None:
            return 0.0
        pos = self._anchor_pos_ms
        if self._playing:
            pos += (self.clock.monotonic() - self._anchor_mono) * 1000.0
        return max(0.0, min(pos, float(self.track.dur_ms)))

    def elapsed_anchor(self) -> dict:
        """Precise anchor, like MRP's elapsedTime + elapsedTimeTimestamp + playbackRate."""
        pos = self.position_ms()
        return {"elapsed_s": pos / 1000.0, "anchor_wall": self.clock.time(),
                "rate": 1.0 if self.is_playing else 0.0}

    def _rebase(self, pos_ms: Optional[float] = None) -> None:
        self._anchor_pos_ms = self.position_ms() if pos_ms is None else float(pos_ms)
        self._anchor_mono = self.clock.monotonic()

    # ---- control (synchronous; tests and the fake scenario drive these) ----
    def play(self) -> None:
        self._rebase()
        self._playing = True
        self._notify()

    def pause(self) -> None:
        self._rebase()
        self._playing = False
        self._notify()

    def seek(self, pos_ms: float) -> None:
        dur = self.track.dur_ms if self.track else 0
        self._rebase(max(0.0, min(float(pos_ms), float(dur))))
        self._notify()

    def set_index(self, i: int) -> None:
        self.index = max(0, min(i, len(self.tracks) - 1))
        self._rebase(0.0)
        self._notify()

    def skip(self, step: int = 1) -> None:
        self.set_index((self.index + step) % len(self.tracks))

    def drop_connection(self, exc: Optional[Exception] = None) -> None:
        if self._device_listener is not None:
            self._device_listener.connection_lost(exc or ConnectionError("fake drop"))

    def tick(self) -> None:
        """Apply due script steps and auto-advance at the end of a track."""
        elapsed = self.clock.monotonic() - self._t0
        while self._script and self._script[0][0] <= elapsed:
            _, action, *args = self._script.pop(0)
            self._run(action, *args)
        track = self.track
        if self.auto_advance and track and self._playing and self.position_ms() >= track.dur_ms:
            if self.index + 1 < len(self.tracks):
                self.set_index(self.index + 1)
            else:
                self._rebase(float(track.dur_ms))
                self._playing = False
                self._notify()

    def _run(self, action: str, *args) -> None:
        if action == "play":
            self.play()
        elif action == "pause":
            self.pause()
        elif action == "seek":
            self.seek(args[0])
        elif action == "next":
            self.skip(1)
        elif action == "prev":
            self.skip(-1)
        elif action == "track":
            self.set_index(int(args[0]))
        elif action == "drop":
            self.drop_connection()
        else:
            raise ValueError(f"unknown fake provider action {action!r}")

    # ---- pyatv-shaped views --------------------------------------------------
    def playing(self) -> FakePlaying:
        t = self.track
        if t is None:
            return FakePlaying(None, None, None, DeviceState.Idle, "", None, None)
        state = DeviceState.Playing if self._playing else DeviceState.Paused
        return FakePlaying(t.title, t.artist, t.album, state, t.hash,
                           int(self.position_ms() // 1000), t.dur_ms // 1000,
                           extra=self.elapsed_anchor())

    def _notify(self) -> None:
        if self._push_listener is not None:
            self._push_listener.playstatus_update(None, self.playing())

    # ---- provider surface used by Engine --------------------------------------
    async def now_playing(self):
        from providers import NowPlaying
        if self.closed:
            return None
        return NowPlaying.from_playing(self.playing())

    async def artwork_bytes(self, width: int = 600, height: int = 600) -> Optional[bytes]:
        t = self.track
        return t.art if t else None

    async def play_pause(self) -> None:
        self.calls.append("play_pause")
        self.pause() if self._playing else self.play()

    async def next(self) -> None:
        self.calls.append("next")
        self.skip(1)

    async def previous(self) -> None:
        self.calls.append("previous")
        self.skip(-1)

    def set_device_listener(self, listener) -> None:
        self._device_listener = listener

    def start_push(self, listener) -> None:
        self._push_listener = listener

    def stop_push(self) -> None:
        self._push_listener = None

    async def close(self) -> None:
        self.stop_push()
        self.closed = True
