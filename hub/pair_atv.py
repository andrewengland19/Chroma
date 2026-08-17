#!/usr/bin/env python3
# =============================================================================
# hub/pair_atv.py
# Scan the LAN for Apple TVs, let you pick one, pair the AirPlay + Companion
# protocols (you type the PIN shown on the TV), and persist credentials to
# ~/.chroma/atv_credentials.json for the hub to reuse.
#
# Run:  python hub/pair_atv.py
# =============================================================================

import asyncio
import sys

import pyatv
from pyatv.const import Protocol as ATVProtocol

from providers import PAIR_PROTOCOLS, save_credentials, load_credentials


async def _scan(loop):
    print("Scanning the LAN for Apple TVs (5s)…")
    results = await pyatv.scan(loop, timeout=5)
    return results


def _pick(results):
    if not results:
        print("\n  ✗  No Apple TVs found. Make sure the TV is awake and on this Wi-Fi.")
        return None
    print("\nFound:")
    for i, r in enumerate(results):
        protos = ", ".join(sorted(s.protocol.name for s in r.services))
        print(f"  [{i}]  {r.name}  @ {r.address}   ({protos})")
    if len(results) == 1:
        print(f"\nSelecting the only device: {results[0].name}")
        return results[0]
    while True:
        raw = input(f"\nSelect device [0-{len(results)-1}]: ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(results):
            return results[int(raw)]
        print("  invalid selection")


async def _pair_protocol(config, protocol, loop) -> str | None:
    """Pair one protocol interactively. Returns credentials string or None."""
    if not any(s.protocol == protocol for s in config.services):
        print(f"  •  {protocol.name}: not offered by this device — skipping")
        return None

    pairing = await pyatv.pair(config, protocol, loop)
    try:
        await pairing.begin()

        if pairing.device_provides_pin:
            pin = input(f"  ⟶  Enter the PIN shown on the TV for {protocol.name}: ").strip()
            pairing.pin(pin)
        else:
            # Rare path: we present a PIN for the user to enter on the TV.
            pairing.pin(1234)
            print(f"  ⟶  Enter PIN 1234 on the TV for {protocol.name}")

        await pairing.finish()
        if pairing.has_paired:
            print(f"  ✓  {protocol.name} paired")
            return pairing.service.credentials
        print(f"  ✗  {protocol.name} pairing did not complete")
        return None
    finally:
        await pairing.close()


async def main():
    loop = asyncio.get_event_loop()

    existing = load_credentials()
    if existing:
        print(f"Existing credentials found for '{existing.get('name')}' "
              f"({existing['identifier']}).")
        if input("Re-pair and overwrite? [y/N]: ").strip().lower() != "y":
            print("Keeping existing credentials. Nothing to do.")
            return

    results = await _scan(loop)
    config = _pick(results)
    if config is None:
        sys.exit(1)

    print(f"\nPairing with {config.name}. Watch the TV for a 4-digit PIN.\n")
    creds: dict[str, str] = {}
    for protocol in PAIR_PROTOCOLS:
        c = await _pair_protocol(config, protocol, loop)
        if c:
            creds[protocol.name] = c

    if not creds:
        print("\n  ✗  No protocols paired. Nothing saved.")
        sys.exit(1)

    save_credentials(str(config.identifier), config.name, creds)
    print(f"\n  ✓  Saved credentials for {config.name} → ~/.chroma/atv_credentials.json")
    print(f"     Protocols: {', '.join(creds)}")
    print("\nNext: quit Apple Music on the Mac, play a track on the Apple TV, then run")
    print("      python hub/spike.py")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
