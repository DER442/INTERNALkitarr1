"""multipad.py - Multi Pads for DX Arranger (pads P-1..P-4 on the panel).

A pad is either
  * a MIDI phrase  (.pad / .mid / .midi)  - played through the same synth as the styles, or
  * an audio sample (.wav / .mp3)          - played by Windows (MCI), at its own speed.

Timing
  * A pad pressed while a style is playing starts on the style's NEXT BEAT, on the style's own clock,
    so it stays locked to the beat and follows every tempo change.
  * A pad pressed while the style is stopped starts at once and follows the panel tempo.
  * MIDI phrases follow the tempo exactly (a bar of the phrase = a bar of the style). Audio samples are
    started on the beat but are NOT time-stretched (that needs an audio library this app does not ship).
  * Pads LOOP until stopped (config.json "multipad_repeat": false = play once). Press a playing pad again to stop it;
    the STOP button stops the beat AND every pad.

Honest limits
  * Chord Match (pad follows the chord you play) is not built: the app has no chord input yet.
  * Yamaha does not publish the .pad layout. Files that are standard MIDI (what .pad files are built on)
    are read; a file with several markers is split into one pad per marker (max 4); anything else becomes one pad.
    Tell me if a real .pad file does not load and I will adapt the reader to it.
"""
import json
import math
import os
import struct
import threading
import time

import sty_play as sp
from sty_parse import parse_track

MIDI_EXTS = (".pad", ".mid", ".midi")
AUDIO_EXTS = (".wav", ".mp3")
PAD_EXTS = MIDI_EXTS + AUDIO_EXTS
SETUP_MARKS = {"SFF1", "SFF2", "SInt"}
# Each pad gets its own MIDI channels (0-based) so two pads can never fight over a voice. Channel 10 (index 9)
# stays the drum channel for phrases that contain drums.
POOL = {1: [0, 1, 2], 2: [3, 4, 5], 3: [6, 7, 8], 4: [10, 11, 12]}
NPADS = 4


def list_files(folder):
    os.makedirs(folder, exist_ok=True)
    names = [n for n in os.listdir(folder) if n.lower().endswith(PAD_EXTS) and os.path.isfile(os.path.join(folder, n))]
    return sorted(names, key=str.lower)


# ---- reading files --------------------------------------------------------------------------------------------
def read_midi_pads(path, slot, shaker=1.0):
    """Return a list of pads from a MIDI-based file. Channels are mapped into this slot's own pool."""
    with open(path, "rb") as f:                         # read-only
        data = f.read()
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI-based file (no MThd)")
    hlen = struct.unpack(">I", data[4:8])[0]
    ppq = struct.unpack(">H", data[12:14])[0]
    if ppq & 0x8000 or ppq == 0:
        raise ValueError("SMPTE time division is not supported")
    pos, events = 8 + hlen, []
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        if cid == b"MTrk":
            events += parse_track(data[pos + 8:pos + 8 + size])
        pos += 8 + size
    events.sort(key=lambda e: e[0])
    marks = [(t, i[1].decode("latin-1").strip()) for t, k, i in events if k == "meta" and i[0] == 6]
    marks = [(t, n) for t, n in marks if n not in SETUP_MARKS]
    end_tick = max([t for t, k, i in events if k == "meta" and i[0] == 0x2F] + [t for t, _, _ in events] + [0])
    if len(marks) >= 2:
        cuts = marks[:NPADS]
        spans = [(n or f"PAD {j + 1}", t, cuts[j + 1][0] if j + 1 < len(cuts) else end_tick)
                 for j, (t, n) in enumerate(cuts)]
    else:
        spans = [(os.path.splitext(os.path.basename(path))[0], 0, end_tick)]
    is_pad_file = path.lower().endswith(".pad")
    pads = []
    for name, a, b in spans:
        pad = _compile(events, ppq, a, b, 1 + len(pads) if len(spans) > 1 else slot, name, is_pad_file, shaker)   # a bank always fills pads 1..N
        if pad:
            pads.append(pad)
    if not pads:
        raise ValueError("no notes found in that file")
    return pads


def _compile(events, ppq, a, b, slot, name, is_pad_file=False, shaker=1.0):
    """Notes of ticks a..b -> [(beat, bytes)], plus the setup (programs, volumes) that came before.
    Rhythm parts are played on the DRUM channel (10): channel 10 always; channel 9 too in a Yamaha .pad file (both are
    rhythm channels there); and any channel whose bank is a drum/SFX kit (bank MSB 127 or 126). Their notes only are
    sent, so a pad never changes the drum kit the style is using."""
    slot = max(1, min(NPADS, slot))
    pool, chmap = POOL[slot], {}
    banks = {}
    for t, k, i in events:
        if k == "chan" and i[0] == 0xB0 and i[2][0] == 0 and i[1] not in banks:
            banks[i[1]] = i[2][1]
    drum_src = {10} | ({9} if is_pad_file else set()) | {ch for ch, msb in banks.items() if msb in (126, 127)}

    def mapch(src):                                     # source channel (1-16) -> our channel index, or None
        if src in drum_src:
            return 9
        if src not in chmap:
            if len(chmap) >= len(pool):
                return None                             # more instruments than channels: extras are dropped
            chmap[src] = pool[len(chmap)]
        return chmap[src]

    notes, setup, last = [], [], 0
    for t, k, i in events:
        if k != "chan":
            continue
        kind, chan, d = i
        ch = mapch(chan)
        if ch is None:
            continue
        drum = chan in drum_src
        if kind in (0x80, 0x90):
            if a <= t < b:
                data = list(d)
                if drum and kind == 0x90 and data[1] > 0 and data[0] in sp.SHAKERS:
                    data[1] = max(1, min(127, int(round(data[1] * shaker))))   # soft shakers (config shaker_level)
                notes.append(((t - a) / ppq, bytes([kind | ch]) + bytes(data)))
                last = max(last, t - a)
        elif not drum and t < b and (t <= a or not notes):   # programs / volume / pan set up before or at the start
            if not (kind == 0xB0 and d[0] in (0, 32)):       # bank select skipped, as for styles
                setup.append(bytes([kind | ch]) + bytes(d))
    if not any(m[0] & 0xF0 == 0x90 and m[2] > 0 for _, m in notes):
        return None
    # loop length: the phrase's own length, but a long silent tail (a bar or more after the last note) is trimmed
    # to whole bars so the pad repeats on the beat instead of waiting
    num, den = sp.time_sig(events)
    bar_ticks = max(1, ppq * 4 * num // den)
    span = max(b - a, last)
    if span - last >= bar_ticks:
        span = math.ceil(last / bar_ticks) * bar_ticks
    beats = max(1.0, math.ceil(span / ppq - 1e-6))
    drums_n = sum(1 for _, m in notes if m[0] & 0x0F == 9 and m[0] & 0xF0 == 0x90 and m[2] > 0)
    inst = sorted(chmap)
    info = (f"{len(notes) // 2 or len(notes)} notes: drums/percussion {drums_n}"
            + (f", pitched instruments on ch {inst}" if inst else "") + f" | loops every {int(beats)} beats")
    return {"name": name.upper(), "kind": "midi", "beats": float(beats), "events": notes, "setup": setup, "info": info,
            "drum_from": sorted(drum_src & {c for _, k2, i2 in events if k2 == "chan" for c in [i2[1]]})}


# ---- audio (Windows MCI) -----------------------------------------------------------------------------------------
class Audio:
    """One MCI device per pad so several samples can sound together. Every MCI call runs on ONE worker thread
    (MCI devices dislike being driven from different threads), and a file Windows cannot open as plain WAV/MP3 is
    retried with the other decoder before giving up."""

    def __init__(self):
        self.ok = os.name == "nt"
        self.last_error = ""
        self.lengths = {}
        if self.ok:
            import ctypes
            import queue
            self.ct = ctypes
            self.mci = ctypes.windll.winmm.mciSendStringW
            self.mci.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
            self.err = ctypes.windll.winmm.mciGetErrorStringW
            self.err.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_uint]
            self.q = queue.Queue()
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            fn, box = self.q.get()
            try:
                box["r"] = fn()
            except Exception as e:                       # never let the worker die
                box["r"], self.last_error = None, repr(e)
            box["ev"].set()

    def _run(self, fn, timeout=4.0):
        if not self.ok:
            return None
        box = {"ev": threading.Event(), "r": None}
        self.q.put((fn, box))
        box["ev"].wait(timeout)
        return box["r"]

    def _mci(self, text, want=False):                    # worker thread only. -> (ok, answer or error text)
        buf = self.ct.create_unicode_buffer(256)
        r = self.mci(text, buf, 255, None)
        if r == 0:
            return True, buf.value if want else ""
        msg = self.ct.create_unicode_buffer(256)
        self.err(r, msg, 255)
        return False, msg.value

    def open(self, slot, path):
        def job():
            self._mci(f"close dxpad{slot}")
            first = "mpegvideo" if path.lower().endswith(".mp3") else "waveaudio"
            tried = []
            for typ in (first, "mpegvideo" if first == "waveaudio" else "waveaudio"):
                ok, msg = self._mci(f'open "{path}" type {typ} alias dxpad{slot}')
                if ok:
                    self._mci(f"set dxpad{slot} time format milliseconds")
                    okl, ln = self._mci(f"status dxpad{slot} length", True)
                    self.lengths[slot] = int(ln) / 1000.0 if okl and ln.isdigit() else None
                    return True
                tried.append(f"{typ}: {msg}")
            self.last_error = "Windows could not open it (" + "; ".join(tried) + ")"
            return False
        return bool(self._run(job))

    def length(self, slot):
        return self.lengths.get(slot)

    def play(self, slot):
        def job():
            ok, msg = self._mci(f"play dxpad{slot} from 0")
            if not ok:
                self.last_error = f"play failed: {msg}"
            return ok
        return bool(self._run(job))

    def playing(self, slot):
        return self._run(lambda: self._mci(f"status dxpad{slot} mode", True)[1] == "playing") is True

    def stop(self, slot):
        self._run(lambda: self._mci(f"stop dxpad{slot}"))

    def close(self, slot):
        self._run(lambda: self._mci(f"close dxpad{slot}"))


# ---- the engine ---------------------------------------------------------------------------------------------------
class PadEngine:
    """get_out() -> synth output; get_sync() -> (TempoClock, quarter) while a style plays, else None;
    get_bpm() -> panel tempo; on_change() is called when a pad starts, stops or is (re)assigned."""

    def __init__(self, get_out, get_sync, get_bpm, folder, state_path, repeat=True, on_change=None, shaker=1.0):
        self.get_out, self.get_sync, self.get_bpm = get_out, get_sync, get_bpm
        self.folder, self.state_path, self.repeat, self.shaker = folder, state_path, repeat, shaker
        self.on_change = on_change or (lambda: None)
        self.pads = {n: None for n in range(1, NPADS + 1)}
        self.token = {n: 0 for n in range(1, NPADS + 1)}      # bumping it stops that pad's thread
        self.live = {n: False for n in range(1, NPADS + 1)}
        self.audio = Audio()
        self.error = ""
        self._restore()

    # -- assignment ------------------------------------------------------------------------------------------------
    def info(self):
        return [{"name": (p or {}).get("name", ""), "kind": (p or {}).get("kind", ""), "playing": self.live[n]}
                for n, p in sorted(self.pads.items())]

    def load(self, name, slot):
        """Put a file from the Multipad folder on a pad. A multi-pad MIDI bank fills pads 1..N. Returns names."""
        name = os.path.basename(name)
        path = os.path.join(self.folder, name)
        if not os.path.isfile(path) or not name.lower().endswith(PAD_EXTS):
            raise ValueError("file not found in the Multipad folder")
        slot = max(1, min(NPADS, int(slot or 1)))
        if name.lower().endswith(AUDIO_EXTS):
            if not self.audio.ok:
                raise ValueError("audio samples play on Windows only")
            self.stop(slot)
            if not self.audio.open(slot, path):
                raise ValueError(self.audio.last_error or "Windows could not open that audio file")
            self.pads[slot] = {"name": os.path.splitext(name)[0].upper(), "kind": "audio", "file": name}
            got = [slot]
        else:
            new = read_midi_pads(path, slot, self.shaker)
            first = 1 if len(new) > 1 else slot
            got = []
            for j, pad in enumerate(new):
                n = first + j
                if n > NPADS:
                    break
                self.stop(n)
                pad["file"], pad["index"] = name, j
                self.pads[n] = pad
                got.append(n)
                print(f"  pad {n} <- {name}: {pad['name']}: {pad['info']}"
                      + (f" (rhythm from channels {pad['drum_from']})" if pad["drum_from"] else ""))
        self._save()
        self.on_change()
        return [self.pads[n]["name"] for n in got]

    def _save(self):
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({str(n): {"file": p["file"], "index": p.get("index", 0)}
                           for n, p in self.pads.items() if p}, f, indent=1)
        except OSError:
            pass

    def _restore(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        for n, v in sorted(saved.items()):
            try:
                n = int(n)
                path = os.path.join(self.folder, v["file"])
                if v["file"].lower().endswith(AUDIO_EXTS):
                    if self.audio.ok and os.path.isfile(path) and self.audio.open(n, path):
                        self.pads[n] = {"name": os.path.splitext(v["file"])[0].upper(), "kind": "audio",
                                        "file": v["file"]}
                elif os.path.isfile(path):
                    bank = read_midi_pads(path, n - v.get("index", 0), self.shaker)
                    pad = bank[min(v.get("index", 0), len(bank) - 1)]
                    pad["file"], pad["index"] = v["file"], v.get("index", 0)
                    self.pads[n] = pad
            except Exception:
                continue

    # -- playing ---------------------------------------------------------------------------------------------------
    def trigger(self, n):
        """Press a pad: start it, or stop it if it is already playing."""
        n = int(n)
        pad = self.pads.get(n)
        if not pad:
            self.error = f"Pad {n} is empty: MENU > STYLES > MULTIPAD to load a file."
            self.on_change()
            return
        if self.live[n]:
            self.stop(n)
            return
        self.error = ""
        print(f"  pad {n}: {pad['name']} ({pad['kind']}){' loops' if self.repeat else ''}")
        self.token[n] += 1
        self.live[n] = True
        self.on_change()
        threading.Thread(target=self._run, args=(n, pad, self.token[n]), daemon=True).start()

    def stop(self, n):
        self.token[n] += 1
        if self.pads.get(n) and self.pads[n]["kind"] == "audio":
            self.audio.stop(n)
        if self.live[n]:
            self.live[n] = False
            self.on_change()

    def stop_all(self):
        for n in self.pads:
            self.stop(n)

    def close(self):
        self.stop_all()
        for n in self.pads:
            self.audio.close(n)

    def _run(self, n, pad, token):
        held, state = set(), {}
        try:
            self._play(n, pad, token, held, state)
        finally:
            out = self.get_out()
            if out and held:
                sp.send_all(out, [bytes([0x80 | ch, note, 0]) for ch, note in held])
            if self.token[n] == token:                  # finished by itself (not stopped or replaced)
                self.live[n] = False
                self.on_change()

    def _play(self, n, pad, token, held, state):
        now = time.perf_counter()
        s = self.get_sync()
        if s:                                           # style running: start on ITS next beat, on ITS clock
            clk, q = s
            G = (math.floor(clk.pos_at(now) / q + 1e-6) + 1) * q
            shared = True
        else:                                           # style stopped: start at once, at the panel tempo
            b = max(20.0, min(300.0, float(self.get_bpm())))
            clk, q, shared = sp.TempoClock(b, b, now), 60.0 / b, False
            G = clk.pos_at(now) + 0.02
        st = {"clk": clk, "shared": shared, "bpm": self.get_bpm()}

        def check():                                    # True = stop; also follows tempo / style start-stop
            if self.token[n] != token:
                return True
            c = st["clk"]
            if st["shared"]:
                cur = self.get_sync()
                if not cur or cur[0] is not c:          # the style stopped: carry on on a private clock
                    t = time.perf_counter()
                    st["clk"] = sp.TempoClock(c.base, max(20.0, min(300.0, float(self.get_bpm()))), t, c.pos_at(t))
                    st["shared"], st["bpm"] = False, self.get_bpm()
            else:
                b = self.get_bpm()
                if b != st["bpm"]:
                    st["bpm"] = b
                    c.set_bpm(max(20.0, min(300.0, float(b))), time.perf_counter())
            return False

        real = lambda g: st["clk"].real(g)

        if pad["kind"] == "audio":
            if sp.wait_until(lambda: real(G), check):
                return
            t_start = time.perf_counter()
            if not self.audio.play(n):
                self.error = f"Pad {n}: {self.audio.last_error}"
                print("  !", self.error)
                self.on_change()
                return
            dur, k = self.audio.length(n), 0
            while True:
                if dur:                                  # known length: restart exactly every `dur`, no gap, no drift
                    nxt = t_start + (k + 1) * dur
                    if sp.wait_until(nxt, check):
                        return
                    if not self.repeat:
                        return
                    k += 1
                    self.audio.play(n)
                else:                                    # length unknown: wait for the end, restart on the next beat
                    while self.audio.playing(n) or time.perf_counter() < t_start + 0.3:
                        if sp.wait_until(time.perf_counter() + 0.01, check):
                            return
                    if not self.repeat:
                        return
                    G = (math.floor(st["clk"].pos_at(time.perf_counter()) / q + 1e-6) + 1) * q
                    if sp.wait_until(lambda: real(G), check):
                        return
                    t_start = time.perf_counter()
                    self.audio.play(n)

        out = self.get_out()
        if out is None:
            return
        length = pad["beats"] * q
        groups = sp.group([(beat * q, m) for beat, m in pad["events"]])
        first = True
        while True:
            for at, msgs in groups:
                if sp.wait_until(lambda a=at: real(G + a), check):
                    return
                batch = (pad["setup"] if first else []) + msgs
                first = False
                sp.send_all(out, batch)
                for m in msgs:
                    sp.track(held, m)
            if sp.wait_until(lambda: real(G + length), check):
                return
            sp.send_all(out, [bytes([0x80 | ch, note, 0]) for ch, note in held])
            held.clear()
            G += length
            if not self.repeat:
                return
