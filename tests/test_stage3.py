"""Stage 3 - complete 128-note / 16-channel recognition, editable maps, overlapping and repeated notes."""
import os, tempfile, shutil, json
from helpers import *
from drum_engine import formats
from drum_engine.drummap import DrumMap
from drum_engine.sampler import SamplerCore, SampleCache, compile_map

tmp = tempfile.mkdtemp()
P = lambda n: os.path.join(tmp, n)
try:
    make_wav(P("hit.wav"), click(2000, 0.25)); make_wav(P("soft.wav"), click(2000, 0.1)); make_wav(P("loud.wav"), click(2000, 0.4))
    make_wav(P("long.wav"), click(44100, 0.05))

    def rig(dm, poly=64, rate=44100):
        cache = SampleCache(rate, resolver=lambda r: P(r)); core = SamplerCore(rate, poly, cache)
        cache.preload({L["sample"] for e in dm.notes.values() for L in e["layers"]}); cache.wait_idle()
        core.set_table(compile_map(dm, rate)); return core

    # --- 1. every note 0-127 on every channel 1-16 is recognised; unassigned ones are COUNTED, not dropped -------------------
    dm = DrumMap("t"); dm.assign(36, "hit.wav"); dm.assign(127, "hit.wav"); dm.assign(0, "hit.wav")   # outside GM drum range
    core = rig(dm)
    ev = [(0, 0x90 | ch, n, 100) for ch in range(16) for n in range(128)]
    core.process(256, ev)
    S = core.stats
    assert S["note_on"] == 2048 and all(c == 16 for c in S["seen"]) and all(c == 128 for c in S["chan_seen"])
    assert S["unmapped"][36] == 0 and S["unmapped"][127] == 0 and S["unmapped"][0] == 0
    assert sum(S["unmapped"]) == 125 * 16 and S["unmapped"][62] == 16
    assert len(core.voices) == 48 or S["stolen"] > 0        # 3 assigned notes x 16 channels = 48 voices
    assert S["stolen"] == 0 and len(core.voices) == 48
    # notes outside GM (0 and 127) really sound
    c2 = rig(dm); o = c2.process(256, [(0, 0x99, 127, 127)]); assert np.abs(o).max() > 0.2
    o = rig(dm).process(256, [(0, 0x9F, 0, 127)]); assert np.abs(o).max() > 0.2            # note 0, channel 16
    # a channel can be switched off, and that is counted too
    c3 = rig(dm); c3.set_channels([10]); c3.process(64, [(0, 0x90, 36, 100), (0, 0x99, 36, 100)])
    assert c3.stats["filtered"] == 1 and len(c3.voices) == 1
    # --- 2. Note On vel 0 == Note Off; one-shot ignores it, gate fades; velocity is respected -----------------------------------
    dm = DrumMap("t"); dm.assign(36, "long.wav"); dm.assign(37, "long.wav"); dm.set_note(37, mode="gate", release_ms=10)
    core = rig(dm)
    core.process(512, [(0, 0x99, 36, 100), (0, 0x99, 37, 100), (100, 0x99, 36, 0), (100, 0x99, 37, 0)])
    assert core.stats["note_off"] == 2
    core.process(4096)
    assert len(core.voices) == 1 and core.voices[0].note == 36        # gate note released and gone, one-shot still ringing
    core.process(4096, [(0, 0x89, 36, 64)]); assert len(core.voices) == 1   # real Note Off: one-shot unaffected
    a = rig(dm).process(64, [(0, 0x99, 36, 127)]); b = rig(dm).process(64, [(0, 0x99, 36, 64)]); c = rig(dm).process(64, [(0, 0x99, 36, 1)])
    ra, rb, rc = a[0, 0], b[0, 0], c[0, 0]
    assert abs(ra / rb - 127 / 64) < 0.01 and rc > 0 and abs(ra / rc - 127) < 0.5, (ra, rb, rc)      # vel 1..127 all distinct, 1 audible
    # --- 3. simultaneous + overlapping + rapid repeated notes: nothing merged, nothing lost -----------------------------------------
    dm = DrumMap("t"); dm.assign(38, "long.wav"); core = rig(dm, poly=4096)
    out = core.process(512, [(0, 0x99, 38, 127)] * 10)               # 10 identical hits at the same instant
    assert len(core.voices) == 10 and abs(out[0, 0] - 0.5) < 1e-3, out[0, 0]    # 10 x 0.05 = 0.5: they ADD
    core = rig(dm, poly=4096); n = 0
    for blk in range(150):                                          # 750 hits, one every ~1 ms, all inside the 1 s sample
        core.process(256, [(i * 50, 0x99, 38, 90) for i in range(5)])
    assert core.stats["note_on"] == 750 and core.stats["stolen"] == 0 and len(core.voices) == 750
    # polyphony limit: oldest voices fade (no click), and it is COUNTED
    core = rig(dm, poly=8); core.process(256, [(i * 10, 0x99, 38, 90) for i in range(20)])
    assert core.stats["stolen"] == 12 and core.stats["note_on"] == 20
    core.process(256); assert len([v for v in core.voices if not v.fade_len]) == 8
    # --- 4. same sample on many notes; velocity layers; overlapping layers stack ---------------------------------------------------------
    dm = DrumMap("t")
    for n in (40, 41, 42): dm.assign(n, "hit.wav")
    core = rig(dm); core.process(64, [(0, 0x99, 40, 127), (0, 0x99, 41, 127), (0, 0x99, 42, 127)]); assert len(core.voices) == 3
    dm = DrumMap("t"); dm.assign(38, "soft.wav", 1, 63); dm.assign(38, "loud.wav", 64, 127, replace=False)
    core = rig(dm)
    lo = core.process(64, [(0, 0x99, 38, 60)])[0, 0]; hi = rig(dm).process(64, [(0, 0x99, 38, 100)])[0, 0]
    assert abs(lo - 0.1 * 60 / 127) < 1e-3 and abs(hi - 0.4 * 100 / 127) < 1e-3          # each range picked its own sample
    dm = DrumMap("t"); dm.assign(38, "soft.wav", 1, 127); dm.assign(38, "loud.wav", 1, 127, replace=False)
    core = rig(dm); core.process(64, [(0, 0x99, 38, 127)]); assert len(core.voices) == 2                 # overlapping ranges layer
    dm = DrumMap("t"); dm.assign(38, "soft.wav", 1, 40)
    core = rig(dm); core.process(64, [(0, 0x99, 38, 120)]); assert core.stats["vel_fallback"] == 1 and len(core.voices) == 1   # gap: nearest layer, reported
    # --- 5. volume, pan, channel volume, choke ---------------------------------------------------------------------------------------------------
    dm = DrumMap("t"); dm.assign(36, "hit.wav", volume=0.5); dm.assign(37, "hit.wav"); dm.set_note(37, pan=1.0); dm.assign(38, "hit.wav"); dm.set_note(38, volume=0)
    core = rig(dm)
    assert abs(core.process(64, [(0, 0x99, 36, 127)])[0, 0] - 0.125) < 1e-3
    o = rig(dm).process(64, [(0, 0x99, 37, 127)]); assert o[0, 0] == 0 and abs(o[0, 1] - 0.25) < 1e-3       # hard right
    o = rig(dm).process(64, [(0, 0x99, 38, 127)]); assert np.abs(o).max() == 0
    core = rig(dm); core.process(64, [(0, 0xB9, 7, 64), (0, 0x99, 36, 127)]); o = core.process(1)
    dm.assign(50, "hit.wav"); core = rig(dm); full = rig(dm).process(8, [(0, 0x99, 50, 127)])[0, 0]
    half = core.process(8, [(0, 0xB9, 7, 64), (0, 0x99, 50, 127)])[0, 0]; assert abs(half / full - 64 / 127) < 0.01   # CC7 scales hits
    dm = DrumMap("t"); dm.assign(42, "long.wav"); dm.assign(46, "long.wav"); dm.apply_gm_names()
    assert dm.notes[42]["choke"] == dm.notes[46]["choke"] != 0 and dm.notes[36]["name"] == "Kick 1" and not dm.notes[36]["layers"]   # names only, no samples
    core = rig(dm); core.process(64, [(0, 0x99, 46, 100)]); core.process(64, [(0, 0x99, 42, 100)]); core.process(4096)
    assert [v.note for v in core.voices] == [42]                     # closed hat choked the open hat
    # --- 6. missing / unsupported samples never crash and are reported ------------------------------------------------------------------------------
    open(P("x.nki"), "wb").write(b"abc")
    dm = DrumMap("t"); dm.assign(36, "nope.wav"); dm.assign(37, "x.nki"); dm.assign(38, "hit.wav")
    core = rig(dm); core.process(64, [(0, 0x99, 36, 100), (0, 0x99, 37, 100), (0, 0x99, 38, 100), (0, 0xC9, 5, 0), (0, 0xF0, 1, 2)])
    assert core.stats["missing"][36] == 1 and core.stats["missing"][37] == 1 and len(core.voices) == 1 and core.stats["ignored"] == 2
    # --- 7. the map: all 128 positions saved, editable, round-trips, bad entries reported ----------------------------------------------------------------
    dm = DrumMap("My Kit", tmp); dm.assign(36, "hit.wav", 1, 100, volume=0.8, pan=-0.2); dm.assign(36, "loud.wav", 101, 127, replace=False)
    dm.set_note(36, name="Kick", choke=3, mode="gate"); dm.save(P("map.json"))
    d = json.load(open(P("map.json"))); assert sorted(map(int, d["notes"])) == list(range(128))
    r = DrumMap.load(P("map.json")); assert r.notes[36]["layers"] == dm.notes[36]["layers"] and r.notes[36]["choke"] == 3 and r.assigned() == [36]
    d["notes"]["200"] = {"layers": []}; d["notes"]["abc"] = {}; d["notes"]["5"]["layers"] = [{"vel_min": 1}]
    json.dump(d, open(P("bad.json"), "w")); r = DrumMap.load(P("bad.json")); assert len(r.warnings) == 3 and r.assigned() == [36]
    try: dm.assign(128, "x.wav"); raise SystemExit("128 must be refused")
    except ValueError: pass
    try: dm.assign(-1, "x.wav"); raise SystemExit("-1 must be refused")
    except ValueError: pass
    print("STAGE 3 OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
