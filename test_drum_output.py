"""Verify app-facing MIDI destination persistence and internal kit MIDI-to-audio mapping offline."""
import os
import shutil
import sys
import tempfile

import numpy as np

# Tests are run as scripts (python tests/test_drum_output.py), like the existing suite.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import click, make_wav

from drum_engine import DrumEngine


tmp = tempfile.mkdtemp()
try:
    kit = os.path.join(tmp, "MyKit")
    os.makedirs(kit)
    make_wav(os.path.join(kit, "36_kick.wav"), click(512, 0.3))
    engine = DrumEngine(tmp, {"sampler_rate": 44100}, log=lambda *a: None)
    kid = engine.folders.add_kit(kit)
    assert engine.load_kit(kid), engine.error
    assert engine.cache.wait_idle(5)
    assert engine.kit.dm.notes[36]["layers"], "Kick MIDI note 36 should auto-map from filename"
    sample_ref = engine.kit.dm.notes[36]["layers"][0]["sample"]
    assert engine.cache.status[sample_ref][0] == "ok"
    # Exercise the actual sample mixer with the same channel-10 Note On bytes sent by styles.
    out = engine.core.process(128, [(0, 0x99, 36, 100)])
    assert np.max(np.abs(out)) > 0, "Mapped MIDI note should produce audio"

    response = engine.command({"op": "set_midi_output", "target": "internal"}, {"local": True})
    assert response["ok"] and response["restart_required"]
    assert engine._read_app_config()["synth"] == "sampler"
    assert engine.state()["midi_output"] == "internal"
    response = engine.command({"op": "set_midi_output", "target": "midiout"}, {"local": True})
    assert response["ok"] and engine._read_app_config()["synth"] == "midiout" and engine.state()["midi_output"] == "midiout"
    assert "error" in engine.command({"op": "set_midi_output", "target": "vst"}, {"local": True})      # the VST output is gone
    assert "error" in engine.command({"op": "set_midi_output", "target": "internal"}, {"local": False})
    engine.shutdown()
    print("DRUM OUTPUT OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
