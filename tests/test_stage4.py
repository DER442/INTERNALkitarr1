"""Stage 4 - Yamaha STY events through the app's own loader/builder into the sampler: notes, velocities, timing."""
import os, tempfile, shutil
from helpers import *
import sty_play as sp
from drum_engine.drummap import DrumMap
from drum_engine.sampler import SamplerCore, SampleCache, compile_map

tmp = tempfile.mkdtemp()
P = lambda n: os.path.join(tmp, n)
RATE = 44100
try:
    # --- a style: Main A (2 bars) with a tempo change, Fill In AA, Ending A; drums on ch 10 AND ch 9, plus a bass on ch 4 ----------------
    S = 480
    main = [(0, 10, 36, 127, 100), (0, 10, 42, 90, 100), (S, 10, 38, 101, 100), (S, 10, 38, 101, 100),   # two identical simultaneous hits
            (S * 2, 10, 99, 64, 100), (S * 3, 10, 0, 1, 100), (S * 3, 9, 127, 77, 100),                # notes far outside the GM drum map, vel 1
            (S * 4, 10, 46, 110, 100), (S * 5, 10, 49, 100, 50), (S * 6, 10, 57, 120, 50), (0, 4, 40, 100, 400)]
    rapid = [(i * 30, 10, 42, 40 + i, 10) for i in range(64)]          # 64 fast hi-hats, 30 ticks apart (a 64th-note roll)
    fill = [(0, 10, 38, 127, 100), (S, 10, 41, 100, 100), (S * 2, 10, 45, 100, 100), (S * 3, 10, 49, 127, 100)]
    ending = [(0, 10, 36, 127, 100), (0, 10, 49, 127, 100)]
    sty = make_sty(P("test.sty"), [("Main A", 2, main + rapid), ("Fill In AA", 1, fill), ("Ending A", 1, ending)],
                   bpm=120, tempo_changes=[(S * 4 + S * 4, 90)])       # tempo drops to 90 at the start of Main A's 2nd bar... (tick 3840)
    ppq, events = sp.load(sty)
    secs, (s0, s1) = sp.find_sections(events, ppq)
    assert set(secs) == {"Main A", "Fill In AA", "Ending A"}, secs
    to_sec = sp.make_clock(events, ppq)

    # --- the sampler: unit-impulse samples, so a hit's onset is exactly one non-zero frame ---------------------------------------------
    imp = np.zeros(300); imp[0] = 1.0; make_wav(P("imp.wav"), imp)
    dm = DrumMap("t")
    for n in range(128): dm.assign(n, "imp.wav")                                 # all 128 notes sound (same impulse)
    def core_for(dm, poly=4096):
        cache = SampleCache(RATE, resolver=lambda r: P(r)); c = SamplerCore(RATE, poly, cache)
        cache.preload(["imp.wav"]); cache.wait_idle(); c.set_table(compile_map(dm, RATE)); return c

    def play(msgs, length_s, core):
        """Deterministic stand-in for the audio callback: timed messages -> sample offsets -> rendered blocks."""
        total = int(length_s * RATE) + 512; blk = 256; out = np.zeros((total + blk, 2), np.float32)
        ev = sorted(((int(round(t * RATE)), m[0], m[1], m[2] if len(m) > 2 else 0) for t, m in msgs), key=lambda e: e[0])
        i = 0
        for start in range(0, total, blk):
            batch = []
            while i < len(ev) and ev[i][0] < start + blk:
                batch.append((ev[i][0] - start,) + ev[i][1:]); i += 1
            out[start:start + blk] = core.process(blk, batch)
        return out

    for sec in secs:
        msgs, length = sp.build(events, *secs[sec], to_sec, True, None, True, None, None)     # exactly what Engine._prepare builds (polish off)
        # (a) what the style contains, straight from the file
        a, b = secs[sec]
        src = [(t, i[0], i[1], i[2]) for t, k, i in events if k == "chan" and a <= t < b and i[0] == 0x90 and i[2][1] > 0 and i[1] in (9, 10)]
        # (b) what reached the player: note numbers and velocities are untouched
        got = [(m[1], m[2]) for _, m in msgs if m[0] == 0x99]
        assert sorted(got) == sorted((d[0], d[1]) for _, _, _, d in src), sec
        core = core_for(dm)
        out = play(msgs, length, core)
        st = core.stats
        assert st["note_on"] == len(src), (sec, st["note_on"], len(src))
        assert sum(st["unmapped"]) == 0 and st["stolen"] == 0
        # (c) timing: every hit lands on the exact frame of its time (impulse onsets, one per distinct time unless simultaneous)
        times = sorted({round(t * RATE) for t, m in msgs if m[0] == 0x99})
        base = to_sec(a)
        onsets = np.flatnonzero(out[:, 0] != 0)
        assert sorted(set(onsets.tolist())) == sorted(times), (sec, len(onsets), len(times))
    # simultaneous identical hits really ADD (two 101-velocity snares at the same instant; quiet impulse so nothing clips)
    msgs, length = sp.build(events, *secs["Main A"], to_sec, True, None, True, None, None)
    imp2 = np.zeros(300); imp2[0] = 0.3; make_wav(P("imp_q.wav"), imp2); dmq = DrumMap("q")
    for n in range(128): dmq.assign(n, "imp_q.wav")
    cq = core_for(dmq); cq.cache.preload(["imp_q.wav"]); cq.cache.wait_idle()
    out = play(msgs, length, cq); f = round([t for t, m in msgs if m[0] == 0x99 and m[1] == 38][0] * RATE)
    assert abs(out[f, 0] - (2 * 0.3 * 101 / 127 + 0.3 * 56 / 127)) < 1e-3, out[f, 0]   # + the roll's hi-hat #16 (vel 56) on the same tick
    out = play(msgs, length, core_for(dm))
    # note 0 (vel 1) and the channel-9 hit on note 127 both sounded; the bass (channel 4) did not (drums_only drops it)
    notes = {m[1] for _, m in msgs if m[0] == 0x99}; assert {0, 127, 99}.issubset(notes) and 40 not in notes
    # tempo change: the second bar is slower, and the player followed it (bar 2 starts later than 2 s after bar 1 would at 120 BPM)
    t_bar2 = to_sec(secs["Main A"][0] + S * 4) - to_sec(secs["Main A"][0]); assert abs(t_bar2 - 2.0) < 1e-6
    t_end = to_sec(secs["Main A"][1]) - to_sec(secs["Main A"][0]); assert abs(t_end - (2.0 + 4 * 60 / 90)) < 1e-4   # tempo is stored as whole microseconds per beat
    # --- a different kit does not touch the style's data: same events, different samples, different sound -----------------------------------
    make_wav(P("alt.wav"), click(300, 0.3)); dm2 = DrumMap("alt")
    for n in range(128): dm2.assign(n, "alt.wav")
    ev_before = [e for e in events]
    c2 = core_for(dm2); cache = c2.cache; cache.preload(["alt.wav"]); cache.wait_idle()
    out2 = play(msgs, length, c2); assert c2.stats["note_on"] == core_for(dm).stats["note_on"] + sum(1 for _, m in msgs if m[0] == 0x99)
    assert events == ev_before and out2.shape == out.shape and not np.array_equal(out, out2)
    # --- the file itself was only read -----------------------------------------------------------------------------------------------------------------
    import hashlib; h1 = hashlib.md5(open(sty, "rb").read()).hexdigest(); sp.load(sty); assert h1 == hashlib.md5(open(sty, "rb").read()).hexdigest()
    print("STAGE 4 OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
