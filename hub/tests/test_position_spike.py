# M1: the position-spike tool records and analyses without hardware (FakeProvider).

import os
import subprocess
import sys

from conftest import REPO

TOOL = os.path.join(REPO, "tools", "position_spike.py")


def test_record_and_analyze_fake(tmp_path):
    out = tmp_path / "spike.jsonl"
    r = subprocess.run([sys.executable, TOOL, "record", "--fake", "--duration", "4", "--out", str(out)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert out.exists() and out.stat().st_size > 0
    md = tmp_path / "spike.md"
    r = subprocess.run([sys.executable, TOOL, "analyze", str(out), "--md", str(md)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    text = md.read_text()
    assert "Apple TV position spike" in text and "SongClock (coarse only)" in text

    sys.path.insert(0, os.path.join(REPO, "tools"))
    import position_spike
    res = position_spike.analyze(str(out))
    assert res["anchor_coverage"] == 1.0 and res["anchor_has_subsecond"] is True
    assert res["coarse_steps_s"] == [1]
    assert res["seeks"] >= 1 and res["track_changes"] >= 1 and res["play_pause_changes"] >= 1
    assert res["drift_ms_abs_max"] is None or res["drift_ms_abs_max"] < 1
    assert res["songclock_precise"]["err_abs_max"] < 1          # fake anchors are exact
    assert res["songclock_coarse_only"]["err_abs_max"] <= 1100
    assert res["songclock_coarse_only"]["max_backstep_ms"] <= 50.001
