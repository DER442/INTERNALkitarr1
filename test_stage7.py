"""Stage 7 - performance and stability. The sound card is replaced by a fake stream that calls the REAL audio callback
at real-time pace, so SamplerOut's own scheduling is measured. (Numbers here are for THIS sandbox CPU: re-run on the
target PC with  python tests/test_stage7.py  to get its figures.)"""
import os, sys, time, types, threading, tempfile, shutil, random, tracemalloc, resource
from helpers import *

# ---- fake sounddevice: paced callbacks, like a real device ---------------------------------------------------------
class FakeStream:
    def __init__(self, samplerate, blocksize, channels, dtype, device, callback, latency):
        self.rate, self.block, self.cb = samplerate, blocksize, callback
        self.out, self.run, self.cpu, self.blocks = [], False, [], 0
    def start(self):
        self.run = True
        self.t = threading.Thread(target=self._loop, daemon=True); self.t.start()
    def _loop(self):
        nxt = time.perf_counter()
        while self.run:
            buf = np.zeros((self.block, 2), np.float32)
            c0 = time.perf_counter(); self.cb(buf, self.block, None, None); c1 = time.perf_counter()
            self.cpu.append(c1 - c0); self.out.append(buf); self.blocks += 1
            nxt += self.block / self.rate
            time.sleep(max(0, nxt - time.perf_counter()))
    def stop(self): self.run = False
    def close(self): self.run = False
sd = types.ModuleType("sounddevice"); sd.OutputStream = FakeStream; sd.query_devices = lambda: []
sys.modules["sounddevice"] = sd

from drum_engine.manager import DrumEngine
tmp = tempfile.mkdtemp(); P = lambda *a: os.path.join(tmp, *a)
RATE = 44100
try:
    # ---- (1) a LARGE kit: 128 notes, 4 velocity layers each, 1.5 s stereo = 512 files, ~135 MB in RAM -------------------------------------
    kit = P("big"); os.makedirs(kit)
    rng = np.random.default_rng(1)
    base = rng.standard_normal((int(1.5 * RATE), 2)) * 0.3 * np.exp(-np.linspace(0, 8, int(1.5 * RATE)))[:, None]
    for n in range(128):
        for v in range(4):
            make_wav(os.path.join(kit, f"{n}_v{v}.wav"), base * (0.5 + 0.1 * v))
    E = DrumEngine(P("app"), {"sampler_rate": RATE, "sampler_block": 256}, log=lambda *a: None)
    E.set_setting("max_ram_mb", 512)
    kid = E.folders.add_kit(kit); t0 = time.time(); ok = E.load_kit(kid); E.cache.wait_idle(120); t_load = time.time() - t0
    assert ok and len(E.cache.samples) == 128, (len(E.cache.samples), E.error)       # one file per note won the auto-assign (the others are listed as extra)
    for n in range(128):                                                             # give every note its 4 velocity layers
        for v in range(4): E.assign(n, f"{n}_v{v}.wav", vel_min=1 + v * 32, vel_max=min(127, 32 + v * 32), replace=(v == 0))
    E.cache.wait_idle(120); mb = E.cache.bytes / 1048576
    print(f"large kit: 128 notes x 4 layers = 512 samples, loaded in {t_load:.1f}s, resident {len(E.cache.samples)} samples = {mb:.0f} MB")
    assert len(E.cache.samples) == 512 and mb < 512

    # ---- (2) real-time stream: CPU per block under heavy load, and end-to-end timing accuracy ----------------------------------------------------
    out = E.make_output(main=True); st = out.stream; assert st.run
    E.set_setting("polyphony", 128)
    time.sleep(0.3)
    random.seed(7); sent = 0; end = time.time() + 8.0
    while time.time() < end:                          # ~ 300 hits per second, dense chords, all 128 notes, 16 channels, mixed velocities
        batch = [bytes([0x90 | random.randrange(16), random.randrange(128), random.randrange(1, 128)]) for _ in range(random.randrange(1, 6))]
        out.send_many(batch); sent += len(batch); time.sleep(0.015)
    time.sleep(0.4)
    cpu = np.array(st.cpu) * 1000; budget = 256 / RATE * 1000
    S = E.core.stats
    print(f"stress {8}s: {sent} hits sent, {S['note_on']} heard | callback CPU per {budget:.1f} ms block: mean {cpu.mean():.2f} ms, p99 {np.percentile(cpu, 99):.2f} ms, max {cpu.max():.2f} ms | voices max {S['max_voices']}, stolen {S['stolen']}, late loads {S['late_loads']}")
    assert S["note_on"] == sent, "every hit must be heard"                          # none dropped between send() and the mixer
    assert S["late_loads"] == 0 and sum(S["missing"]) == 0 and out.error == ""
    assert np.percentile(cpu, 99) < budget, "99% of blocks must render faster than real time"
    # ---- (3) timing: Note On sent at a known instant is audible within one block (+ the render) --------------------------------------------------------
    E.core.panic(); time.sleep(0.2); E.core.master = 1.0
    imp = np.zeros(300); imp[0] = 0.9; make_wav(P("big", "imp.wav"), imp); E.assign(5, "imp.wav"); E.cache.wait_idle()
    lat = []
    for _ in range(25):
        n0 = st.blocks; t_send = time.perf_counter(); out.send(bytes([0x99, 5, 127]))
        while st.blocks < n0 + 4: time.sleep(0.001)
        for i in range(n0, st.blocks):
            nz = np.flatnonzero(st.out[i][:, 0] > 0.5)
            if nz.size:
                # callback i rendered the block that starts at ~ (t_cb - block); offset inside it
                lat.append(None if False else (i - n0, int(nz[0]))); break
        time.sleep(0.05)
    assert len(lat) == 25, "every test hit must be audible"
    blocks_late = max(b for b, _ in lat)
    print(f"timing: a hit is audible in the first or second block after send in {sum(1 for b,_ in lat if b<=1)}/25 cases (max {blocks_late} blocks = {blocks_late*256/RATE*1000:.1f} ms of scheduling)")
    assert blocks_late <= 2

    # ---- (4) intervals between hits are preserved to within a sample or two of jitter-free offsets (the VstOut scheme) --------------------------------
    # (the fake clock is a Python thread, so jitter here is worse than a real audio callback; the bound is deliberately loose)
    E.core.panic(); time.sleep(0.2); n0 = st.blocks
    t_start = time.perf_counter()
    for k in range(20):
        out.send(bytes([0x99, 5, 127])); time.sleep(0.1 - 0.0005)
    time.sleep(0.2)
    buf = np.concatenate(st.out[n0:]); on = np.flatnonzero(buf[:, 0] > 0.5)
    first_of_each = [on[0]] + [int(on[i]) for i in range(1, len(on)) if on[i] - on[i - 1] > 1000]
    gaps = np.diff(first_of_each) / RATE * 1000
    print(f"timing: 20 hits sent every ~100 ms -> heard intervals mean {gaps.mean():.2f} ms, stdev {gaps.std():.2f} ms (a block is {256/RATE*1000:.1f} ms)")
    assert len(first_of_each) == 20 and gaps.std() < 6.0

    # ---- (5) hostile / garbage input never kills the audio thread ---------------------------------------------------------------------------------------------
    for _ in range(3000):
        m = bytes(random.randrange(256) for _ in range(random.randrange(0, 5)))
        try: out.send(m)
        except Exception as e: raise SystemExit(f"send() raised on {m!r}: {e}")
    out.send(bytes([0x99, 200, 100])); out.send(bytes([0x99, 5])); out.send(bytes([0xF0, 1, 2, 0xF7]))      # data byte > 127, short message, sysex
    time.sleep(0.5); assert st.run and out.error == "", out.error
    # ---- (6) LONG offline session: 3 minutes of audio of dense random drumming; memory and voices stay bounded --------------------------------------------
    st.run = False; time.sleep(0.1); st.out.clear(); st.cpu.clear()      # the fake card keeps every block it 'plays': stop it so it cannot look like a leak
    core = E.core; core.reset_stats(); core.polyphony = 64
    tracemalloc.start(); blocks = int(3 * 60 * RATE / 256); worst = 0; t0 = time.time(); m0 = None
    rnd = random.Random(3)
    for b in range(blocks):
        ev = sorted((rnd.randrange(256), 0x90 | rnd.randrange(16), rnd.randrange(128), rnd.randrange(0, 128)) for _ in range(rnd.randrange(0, 4)))
        c0 = time.perf_counter(); o = core.process(256, ev); worst = max(worst, time.perf_counter() - c0)
        assert o.shape == (256, 2) and np.isfinite(o).all() and np.abs(o).max() <= 1.0
        if b == blocks // 10: m0 = tracemalloc.get_traced_memory()[0]
        assert len(core.voices) <= 70
    m1 = tracemalloc.get_traced_memory()[0]; tracemalloc.stop()
    el = time.time() - t0
    print(f"3 min of audio rendered in {el:.1f}s ({3*60/el:.0f}x real time), worst block {worst*1000:.2f} ms, heap growth after warm-up {(m1-m0)/1024:.0f} KB, stolen {core.stats['stolen']}, notes {core.stats['note_on']}")
    assert (m1 - m0) < 2 * 1024 * 1024, "memory must not grow during a long session"
    E.shutdown()
    print("STAGE 7 OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
