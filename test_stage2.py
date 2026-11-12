"""Stage 2 - WAV loading, honest reporting, previews. (Also covers the Stage 6 SoundFont reader.)"""
import os, tempfile, shutil
from helpers import *
from drum_engine import formats
from drum_engine.sampler import SamplerCore, SampleCache

tmp = tempfile.mkdtemp()
P = lambda n: os.path.join(tmp, n)
try:
    src = tone(2000)
    # --- every supported WAV flavour decodes to the same signal -------------------------------------------------
    cases = {"16m.wav": dict(bits=16), "24m.wav": dict(bits=24), "32i.wav": dict(bits=32), "8m.wav": dict(bits=8),
             "f32.wav": dict(bits=32, tag=3), "f64.wav": dict(bits=64, tag=3), "ext16.wav": dict(bits=16, extensible=True),
             "chunks.wav": dict(bits=16, extra_chunk=True), "badsize.wav": dict(bits=16, bad_size=True)}
    for fn, kw in cases.items():
        make_wav(P(fn), src, **kw)
        s = formats.load(P(fn), 44100)
        tol = 0.02 if kw["bits"] == 8 else 1e-3
        assert s.data.shape == (2000,) and np.abs(s.data - src).max() < tol, (fn, np.abs(s.data - src).max())
    # stereo: different channels are kept; identical channels are stored as mono (half the RAM)
    make_wav(P("st.wav"), np.stack([src, -src], 1)); s = formats.load(P("st.wav"), 44100)
    assert s.data.shape == (2000, 2) and np.abs(s.data[:, 1] + src).max() < 1e-3
    make_wav(P("dual.wav"), np.stack([src, src], 1)); assert formats.load(P("dual.wav"), 44100).data.ndim == 1
    # sample-rate conversion to the engine rate
    make_wav(P("r22.wav"), tone(2205, rate=22050), rate=22050); s = formats.load(P("r22.wav"), 44100)
    assert abs(s.data.shape[0] - 4410) <= 1
    # metadata without decoding
    i = formats.probe(P("chunks.wav")); assert i.status == "ok" and i.frames == 2000 and i.meta.get("root_note") == 60 and i.rate == 44100
    # --- honest failures ---------------------------------------------------------------------------------------------
    open(P("junk.wav"), "wb").write(b"this is not audio at all")
    open(P("pack.ncw"), "wb").write(b"\x01\xa8\x9e\xd6" + b"\0" * 50)
    open(P("song.mp3"), "wb").write(b"ID3"); open(P("inst.nki"), "wb").write(b"\x00" * 8)
    make_wav(P("empty.wav"), src, header_only=True)
    make_wav(P("adpcm.wav"), src, tag=2)
    for fn, want in [("junk.wav", "unreadable"), ("pack.ncw", "unreadable"), ("inst.nki", "unsupported"), ("song.mp3", "unsupported"),
                     ("empty.wav", "unreadable"), ("adpcm.wav", "unsupported"), ("nofile.wav", "missing")]:
        i = formats.probe(P(fn)); assert i.status == want and i.reason, (fn, i.status, i.reason)
        if want != "missing":
            try: formats.load(P(fn), 44100); raise SystemExit("load must refuse " + fn)
            except (formats.FormatError, formats.UnsupportedFormat): pass
    assert "NCW" in formats.probe(P("pack.ncw")).reason and "Kontakt" in formats.probe(P("inst.nki")).reason
    # --- scan lists good AND bad files, nothing hidden ------------------------------------------------------------------
    infos = {os.path.basename(i.ref): i.status for i in formats.scan(tmp)}
    assert infos["16m.wav"] == "ok" and infos["junk.wav"] == "unreadable" and infos["pack.ncw"] == "unreadable" and infos["inst.nki"] == "unsupported", infos
    # --- SoundFont sample pool (Stage 6) ---------------------------------------------------------------------------------
    a, b = (tone(1000) * 30000).astype(np.int16), (tone(800, 220) * 20000).astype(np.int16)
    make_sf2(P("pool.sf2"), [("Kick A", a, 44100), ("Snare B", b, 32000)])
    mem = formats.scan(tmp); names = {i.name: i for i in mem if i.ext == ".sf2"}
    assert set(names) == {"Kick A", "Snare B"} and names["Kick A"].status == "ok" and names["Snare B"].rate == 32000
    s = formats.load(P("pool.sf2") + "#0", 44100); assert np.abs(s.data - a / 32768.0).max() < 1e-4
    s = formats.load(P("pool.sf2") + "#1", 44100); assert abs(s.data.shape[0] - round(800 * 44100 / 32000)) <= 1
    make_sf2(P("comp.sf2"), [("x", a, 44100)], major=3)
    i = [x for x in formats.scan(tmp) if x.ref.endswith("comp.sf2")][0]; assert i.status == "unsupported" and "SF3" in i.reason
    # --- the cache loads off-thread, reports failures, and a bad file does not stop the others ---------------------------------
    cache = SampleCache(44100, resolver=lambda r: P(r))
    cache.preload(["16m.wav", "junk.wav", "pack.ncw", "gone.wav", "pool.sf2#0"]); assert cache.wait_idle()
    got = {r: cache.status[r][0] for r in cache.status}
    assert got == {"16m.wav": "ok", "junk.wav": "unreadable", "pack.ncw": "unreadable", "gone.wav": "missing", "pool.sf2#0": "ok"}, got
    # --- preview through the real mixer: audible and the right level ------------------------------------------------------------
    core = SamplerCore(44100, cache=cache)
    core.preview(cache.samples["16m.wav"], gain=1.0)
    out = core.process(512); assert abs(np.abs(out[:, 0]).max() - 0.5) < 0.01 and np.allclose(out[:, 0], out[:, 1])
    # --- RAM cap: oldest unused sample is dropped, then reloaded on demand --------------------------------------------------------
    big = SampleCache(44100, max_mb=0.02, resolver=lambda r: P(r))        # ~21 KB: room for two 2000-frame samples
    for n in ("16m.wav", "24m.wav", "32i.wav"): big.request(n)
    big.wait_idle(); assert big.bytes <= big.max_bytes and len(big.samples) == 2, (big.bytes, list(big.samples))
    gone = [n for n in ("16m.wav", "24m.wav", "32i.wav") if n not in big.samples][0]
    assert big.status[gone][0] == "on_demand"; big.get(gone); big.wait_idle(); assert gone in big.samples
    print("STAGE 2 OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
