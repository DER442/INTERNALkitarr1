"""NCW support. The encoder below is written from the layout documented in drum_engine/ncw.py (which comes from
ncw2wav.py), so these tests prove the decoder follows that layout exactly and fails honestly on damage. They do NOT prove
the layout matches Native Instruments' real files - that needs a real .ncw."""
import os, struct, tempfile, shutil
from helpers import *
from drum_engine import formats, ncw
from drum_engine.sampler import SampleCache, SamplerCore
from drum_engine.manager import DrumEngine

B = 512


def pack(vals, width):
    v = (np.asarray(vals, np.int64) & ((1 << width) - 1))[:, None] >> np.arange(width) & 1
    return np.packbits(v.astype(np.uint8).reshape(-1), bitorder="little").tobytes()


def need_width(a):
    m = int(np.abs(a).max()) if len(a) else 0
    return max(2, m.bit_length() + 1)


def subblock(samples, mode, flags=0, sig=0x3E9A0C16, cut=None):
    s = np.zeros(B, np.int64)
    s[:len(samples)] = samples
    s[len(samples):] = samples[-1]                                 # pad a short last block with its last value
    if mode == "const":
        return struct.pack("<IihHI", sig, int(s[0]), 0, flags, 0)
    if mode == "delta":
        d = np.zeros(B, np.int64)
        d[:B - 1] = np.diff(s)
        d[B - 1] = 12345 & 0x3FF                                   # the 512th delta is stored but must never be used
        w = need_width(d)
        payload = pack(d, w)
        hdr = struct.pack("<IihHI", sig, int(s[0]), w, flags, 0)
    else:                                                          # raw
        w = need_width(s)
        payload = pack(s, w)
        hdr = struct.pack("<IihHI", sig, 0, -w, flags, 0)
    if cut is not None:                                            # keep only the bytes holding the first `cut` values
        payload = payload[:(cut * w + 7) // 8]
    return hdr + payload


def make_ncw(path, chans, bits=16, rate=44100, modes=("delta",), mid_side=False, sig=0x3E9A0C16, magic4=0x31,
             cut_last=None):
    """chans: list of int arrays (one per channel; for mid_side these are mid and side)."""
    n = len(chans[0]); nb = (n + B - 1) // B
    blocks = []
    for b in range(nb):
        part = b""
        for c, ch in enumerate(chans):
            seg = ch[b * B:(b + 1) * B]
            cut = cut_last if (b == nb - 1 and c == len(chans) - 1) else None
            part += subblock(seg, modes[(b + c) % len(modes)], flags=1 if (mid_side and c == 0) else 0, sig=sig, cut=cut)
        blocks.append(part)
    table, pos = [], 0
    for blk in blocks:
        table.append(pos); pos += len(blk)
    table.append(pos)
    boff = 120
    doff = boff + 4 * len(table)
    head = b"\x01\xa8\x9e\xd6" + bytes([magic4]) + b"\x01\x00\x00" + struct.pack("<HHII", len(chans), bits, rate, n)
    head += struct.pack("<III", boff, doff, pos)
    head = head.ljust(120, b"\0")
    with open(path, "wb") as f:
        f.write(head + struct.pack(f"<{len(table)}I", *table) + b"".join(blocks))


rng = np.random.default_rng(7)
walk = lambda n, step=900: np.cumsum(rng.integers(-step, step + 1, n)).astype(np.int64).clip(-30000, 30000)

tmp = tempfile.mkdtemp()
P = lambda *a: os.path.join(tmp, *a)
try:
    # --- mono 16-bit, delta + raw + constant blocks, a partial last block --------------------------------------
    a = walk(1300)
    a[512:1024] = 777                                              # a flat block is exactly representable as 'const'
    make_ncw(P("m.ncw"), [a], modes=("delta", "const", "raw"))
    s = formats.load(P("m.ncw"), 44100)
    assert s.data.shape == (1300,) and s.data.dtype == np.float32 and np.array_equal(np.round(s.data * 32768).astype(int), a)
    i = formats.probe(P("m.ncw")); assert i.status == "ok" and (i.frames, i.rate, i.channels, i.bits) == (1300, 44100, 1, 16)
    # --- 24-bit -----------------------------------------------------------------------------------------------
    b24 = (walk(700, 40000) * 100).clip(-8000000, 8000000)
    make_ncw(P("m24.ncw"), [b24], bits=24, modes=("raw", "delta"))
    assert np.array_equal(np.round(formats.load(P("m24.ncw"), 44100).data * 8388608).astype(np.int64), b24)
    # --- stereo: independent channels, and mid/side ----------------------------------------------------------------
    l, r = walk(1000), walk(1000, 300)
    make_ncw(P("st.ncw"), [l, r], modes=("delta", "raw"))
    s = formats.load(P("st.ncw"), 44100); got = np.round(s.data * 32768).astype(int)
    assert s.data.shape == (1000, 2) and np.array_equal(got[:, 0], l) and np.array_equal(got[:, 1], r)
    mid, side = walk(1000, 400) // 2, walk(1000, 200) // 4
    make_ncw(P("ms.ncw"), [mid, side], mid_side=True)
    got = np.round(formats.load(P("ms.ncw"), 44100).data * 32768).astype(int)
    assert np.array_equal(got[:, 0], mid + side) and np.array_equal(got[:, 1], mid - side)
    make_ncw(P("dual.ncw"), [l, l]); assert formats.load(P("dual.ncw"), 44100).data.ndim == 1      # identical channels -> mono
    # --- tolerated variations ------------------------------------------------------------------------------------------
    make_ncw(P("alt_sig.ncw"), [a], sig=0x160C9A3E, modes=("delta",)); assert formats.probe(P("alt_sig.ncw")).status == "ok"
    make_ncw(P("v30.ncw"), [a], magic4=0x30, modes=("delta",)); assert formats.probe(P("v30.ncw")).status == "ok"
    # last block may omit padding bytes after the last real frame ...
    make_ncw(P("short_ok.ncw"), [walk(600)], modes=("raw",), cut_last=88)        # 600 frames = 512 + 88: only the 88 real ones are kept
    assert formats.probe(P("short_ok.ncw")).status == "ok"; formats.load(P("short_ok.ncw"), 44100)
    # --- honest failures -----------------------------------------------------------------------------------------------
    make_ncw(P("trunc_blk.ncw"), [walk(600)], modes=("raw",), cut_last=10)      # real samples missing
    raw = open(P("m.ncw"), "rb").read()
    open(P("cut.ncw"), "wb").write(raw[:len(raw) - 40])
    bad = bytearray(raw); bad[120 + 4 * 4:120 + 4 * 4 + 4] = b"\0\0\0\0"           # wreck the first sub-block signature
    open(P("badsig.ncw"), "wb").write(bytes(bad))
    ver = bytearray(raw); ver[4] = 0x99; open(P("badver.ncw"), "wb").write(bytes(ver))
    for fn in ("trunc_blk.ncw", "cut.ncw", "badsig.ncw", "badver.ncw", "pack.ncw"):
        if fn == "pack.ncw": open(P(fn), "wb").write(b"\x01\xa8\x9e\xd6" + b"\0" * 50)
        i = formats.probe(P(fn))
        if fn != "trunc_blk.ncw":                                  # (that one is only found when decoding)
            assert i.status == "unreadable" and i.reason.startswith("NCW"), (fn, i.status, i.reason)
        try: formats.load(P(fn), 44100); raise SystemExit("load must refuse " + fn)
        except formats.FormatError as err: assert "NCW" in str(err), (fn, err)
    # --- resampling, scan, cache, mixer, and a whole kit ---------------------------------------------------------------------
    assert abs(formats.load(P("m.ncw"), 48000).data.shape[0] - round(1300 * 48000 / 44100)) <= 1
    assert {os.path.basename(x.ref): x.status for x in formats.scan(tmp)}["st.ncw"] == "ok"
    cache = SampleCache(44100, resolver=lambda r: P(r)); cache.preload(["m.ncw", "cut.ncw"]); assert cache.wait_idle()
    assert cache.status["m.ncw"][0] == "ok" and cache.status["cut.ncw"][0] == "unreadable"
    core = SamplerCore(44100, cache=cache); core.preview(cache.samples["m.ncw"], gain=1.0)
    out = core.process(512); assert np.abs(out).max() > 0.01
    kit = P("kit"); os.makedirs(kit); app = P("app"); os.makedirs(app)
    make_ncw(os.path.join(kit, "36_kick.ncw"), [np.full(2000, 12000)], modes=("raw", "delta"))
    make_wav(os.path.join(kit, "snare.wav"), click(2000, 0.3))
    E = DrumEngine(app, {}); kid = E.folders.add_kit(kit); assert E.load_kit(kid), E.error
    assert E.kit.dm.assigned() == [36, 38], E.kit.dm.assigned()
    E.cache.wait_idle(); assert E.note_rows()[36]["state"] == "assigned" and not [p for p in E.pool() if p["status"] != "ok"]
    ref = E.kit.dm.notes[36]["layers"][0]["sample"]
    assert abs(float(E.cache.samples[ref].data[100]) - 12000 / 32768) < 1e-6
    print("NCW OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
