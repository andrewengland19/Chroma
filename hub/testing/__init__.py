# =============================================================================
# hub/testing — fakes so everything runs without the Apple TV, bulbs or board.
#
#   FakeClock      manual monotonic + wall clock (advance() in tests)
#   FakeProvider   scripted tracks with position, pause/resume/seek/track change
#   FakeLight/FakeLifxLAN  lifxlan stand-ins that record every call
#   make_art       deterministic album art (solid-colour blocks)
#
# Used by hub/tests and by `chromad --fake`.
# =============================================================================
