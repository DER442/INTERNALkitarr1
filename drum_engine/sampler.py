"""Stages 2-5 - the real-time sample player.

  SampleCache  decoded samples, loaded OFF the audio thread, with a RAM cap (least-recently-used samples are
               dropped when the cap is hit and re-loaded on demand).
  SamplerCore  pure numpy mixer: Note On/Off -> voices. No audio device needed, so it is fully testable offline.
  SamplerOut   the sound output: same interface as the other DX-Arranger outputs (send / send_many / set_gain /
               close). Messages are stamped with their arrival time and placed inside the next audio block at the
               same offset (block-aligned scheduling, no jitter): a fixed delay of one block, no jitter.

Rules the engine keeps (and reports in stats instead of hiding):
  * every note 0-127 on every channel is recognised and counted, whether or not it has a sample;
  * a note with no sample is counted in stats['unmapped'][note]; an unusable sample in stats['missing'][note];
  * overlapping notes and fast repeats are just more voices - nothing is merged or dropped;
  * only when polyphony is exhausted is the OLDEST voice faded out (1.5 ms, no click) and stats['stolen'] counted.
"""
import collections
import queue
import threading
import time

import numpy as np

from . import formats


# ============================== sample cache ==============================================================
class SampleCache:
    def __init__(self, rate, max_mb=512, resolver=None):
        self.rate = rate
        self.max_bytes = int(max_mb * 1024 * 1024)
        self.resolver = resolver or (lambda ref: ref)
        self.samples = {}                    # ref -> Sample (resident)
        self.status = {}                     # ref -> (state, reason); states: ok pending on_demand missing unsupported unreadable
        self.used = {}                       # ref -> last-use counter (for eviction)
        self.bytes = 0
        self._tick = 0
        self._q = queue.Queue()
        self._queued = set()
        self.lock = threading.Lock()
        self.generation = 0                  # bumped by reset(); a worker finishing an old job ignores its result
        self.worker = threading.Thread(target=self._run, daemon=True, name="drum-sample-loader")
        self.worker.start()

    # -- audio-thread side: never blocks, never reads a file --------------------------------------------
    def get(self, ref):
        s = self.samples.get(ref)
        if s is not None:
            self._tick += 1
            self.used[ref] = self._tick
            return s
        st = self.status.get(ref)
        if st is None or st[0] == "on_demand":
            self.request(ref)
        return None

    # -- control side ----------------------------------------------------------------------------------------
    def request(self, ref):
        with self.lock:
            if ref in self._queued or ref in self.samples:
                return
            self._queued.add(ref)
            self.status[ref] = ("pending", "loading")
        self._q.put((self.generation, ref))

    def preload(self, refs):
        for r in refs:
            self.request(r)

    def reset(self):
        with self.lock:
            self.generation += 1
            self.samples, self.status, self.used, self._queued = {}, {}, {}, set()
            self.bytes = 0
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def idle(self):
        return self._q.empty() and not self._queued

    def wait_idle(self, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.idle():
                return True
            time.sleep(0.01)
        return False

    def _evict(self, need):
        """Drop least-recently-used resident samples until `need` more bytes fit under the cap."""
        for ref in sorted(self.samples, key=lambda r: self.used.get(r, 0)):
            if self.bytes + need <= self.max_bytes:
                break
            s = self.samples.pop(ref)
            self.bytes -= s.nbytes
            self.status[ref] = ("on_demand", "not resident (RAM limit); loads when first played")

    def _run(self):
        while True:
            gen, ref = self._q.get()
            try:
                if gen != self.generation:
                    continue
                try:
                    smp = formats.load(self.resolver(ref), self.rate)
                except formats.UnsupportedFormat as err:
                    res = ("unsupported", str(err))
                except FileNotFoundError:
                    res = ("missing", "file not found")
                except (formats.FormatError, OSError, MemoryError, ValueError) as err:
                    res = ("unreadable", str(err) or err.__class__.__name__)
                except Exception as err:                       # a broken sample must never kill the loader
                    res = ("unreadable", f"{err.__class__.__name__}: {err}")
                else:
                    with self.lock:
                        if gen == self.generation:
                            if smp.nbytes > self.max_bytes:
                                res = ("unreadable", "larger than the RAM limit for samples")
                            else:
                                self._evict(smp.nbytes)
                                self.samples[ref] = smp
                                self.bytes += smp.nbytes
                                self._tick += 1
                                self.used[ref] = self._tick
                                res = ("ok", "")
                        else:
                            continue
                if gen == self.generation:
                    self.status[ref] = res
            finally:
                with self.lock:
                    self._queued.discard(ref)


# ============================== compiled map ======================================================================
class _Layer:
    __slots__ = ("ref", "vmin", "vmax", "gl", "gr", "start")

    def __init__(self, ref, vmin, vmax, gl, gr, start):
        self.ref, self.vmin, self.vmax, self.gl, self.gr, self.start = ref, vmin, vmax, gl, gr, start


class _Slot:
    __slots__ = ("layers", "gate", "choke", "rel")

    def __init__(self, layers, gate, choke, rel):
        self.layers, self.gate, self.choke, self.rel = layers, gate, choke, rel


def balance(pan):
    pan = max(-1.0, min(1.0, pan))
    return min(1.0, 1.0 - pan), min(1.0, 1.0 + pan)


def compile_map(dm, rate):
    """DrumMap -> list of 128 _Slot (or None for a note with no layers). Cheap; done on every edit."""
    table = [None] * 128
    for n in range(128):
        e = dm.notes[n]
        if not e["layers"]:
            continue
        layers = []
        for L in e["layers"]:
            gl, gr = balance(e["pan"] + L["pan"])
            g = e["volume"] * L["volume"]
            layers.append(_Layer(L["sample"], L["vel_min"], L["vel_max"], gl * g, gr * g,
                                 int(L["start_ms"] * rate / 1000.0)))
        table[n] = _Slot(tuple(layers), e["mode"] == "gate", e["choke"], max(32, int(e["release_ms"] * rate / 1000.0)))
    return table


# ============================== the mixer =====================================================================================
_AR = np.arange(65536, dtype=np.float32)          # shared ramp base (no allocation per fading voice)


class _Voice:
    __slots__ = ("data", "pos", "g", "ch", "note", "gate", "choke", "born", "fade_len", "fade_pos")

    def __init__(self, data, pos, gl, gr, ch, note, gate, choke, born):
        self.data, self.pos = data, pos
        self.g = np.array([gl, gr], np.float32)       # left/right gain, applied in ONE fused multiply
        self.ch, self.note, self.gate, self.choke, self.born = ch, note, gate, choke, born
        self.fade_len = 0
        self.fade_pos = 0

    def fade(self, frames):
        if not self.fade_len:
            self.fade_len, self.fade_pos = max(1, int(frames)), 0


class SamplerCore:
    def __init__(self, rate=44100, polyphony=64, cache=None):
        self.rate = rate
        self.cache = cache or SampleCache(rate)
        self.table = [None] * 128
        self.polyphony = int(polyphony)
        self.voices = []
        self.incoming = collections.deque()          # voices created off the audio thread (previews)
        self.channels = frozenset(range(16))         # 0-based channels that trigger samples
        self.chvol = [127] * 16                      # MIDI CC7 per channel (style volume / VOL knobs); no CC7 received = unscaled
        self.master = 1.0                            # drum master (0-2), from the DRUM ENGINE page
        self.outgain = 1.0                           # the app's master / MUTE (set_gain)
        self.vel_curve = 1.0
        self._g_prev = 1.0
        self._born = 0
        self.last_hit = [0.0] * 128
        self.reset_stats()

    def reset_stats(self):
        self.stats = dict(note_on=0, note_off=0, stolen=0, late_loads=0, vel_fallback=0, ignored=0, clipped_blocks=0,
                          max_voices=0, peak=0.0, filtered=0,
                          seen=[0] * 128, unmapped=[0] * 128, missing=[0] * 128,
                          chan_seen=[0] * 16)

    # ---- configuration (control thread) -------------------------------------------------------------------------
    def set_table(self, table):
        self.table = table                           # one assignment: the audio thread sees old or new, never half

    def set_channels(self, chans):
        self.channels = frozenset(c - 1 for c in chans if 1 <= c <= 16)

    # ---- previews: a sample played straight, bypassing the map ----------------------------------------------------------
    def preview(self, sample, gain=0.8):
        self._born += 1
        self.incoming.append(_Voice(sample.data, 0, gain, gain, -1, -1, False, 0, self._born))

    def panic(self):
        for v in self.voices:
            v.fade(64)

    # ---- the audio block ------------------------------------------------------------------------------------------------------
    def process(self, frames, events=()):
        """events: iterable of (offset_frame, status, data1, data2), sorted by offset. Returns float32 (frames, 2)."""
        out = np.zeros((frames, 2), np.float32)
        while self.incoming:
            self.voices.append(self.incoming.popleft())
        cur = 0
        for off, st, d1, d2 in events:
            off = max(cur, min(frames, off))
            if off > cur:
                self._mix(out, cur, off)
                cur = off
            self._event(st, d1, d2)
        if cur < frames:
            self._mix(out, cur, frames)
        g = self.master * self.outgain
        if g != self._g_prev:
            out *= np.linspace(self._g_prev, g, frames, dtype=np.float32)[:, None]
            self._g_prev = g
        elif g != 1.0:
            out *= g
        pk = float(np.abs(out).max()) if frames else 0.0
        st_ = self.stats
        if pk > st_["peak"]:
            st_["peak"] = pk
        if pk > 1.0:
            st_["clipped_blocks"] += 1
            np.clip(out, -1.0, 1.0, out=out)
        n = len(self.voices)
        if n > st_["max_voices"]:
            st_["max_voices"] = n
        return out

    def _mix(self, out, a, b):
        n = b - a
        keep = []
        for v in self.voices:
            d, p = v.data, v.pos
            chunk = d[p:p + n]
            m = chunk.shape[0]
            if m == 0:
                continue
            w = (chunk[:, None] if chunk.ndim == 1 else chunk) * v.g          # (m, 2): gains applied to both channels at once
            if v.fade_len:
                ramp = 1.0 - (_AR[:m] + v.fade_pos) / v.fade_len
                np.maximum(ramp, 0.0, out=ramp)
                w *= ramp[:, None]
                v.fade_pos += m
            out[a:a + m] += w
            v.pos = p + m
            if v.pos < d.shape[0] and not (v.fade_len and v.fade_pos >= v.fade_len):
                keep.append(v)
        self.voices = keep

    # ---- MIDI ---------------------------------------------------------------------------------------------------------------------
    def _event(self, st, d1, d2):
        kind, ch = st & 0xF0, st & 0x0F
        if kind == 0x90 and d2 > 0:
            self._note_on(ch, d1 & 0x7F, d2 & 0x7F)
        elif kind == 0x80 or kind == 0x90:                       # Note On with velocity 0 is a Note Off
            self.stats["note_off"] += 1
            for v in self.voices:
                if v.gate and v.ch == ch and v.note == d1 and not v.fade_len:
                    v.fade(self._rel_for(d1))
        elif kind == 0xB0:
            if d1 == 7:
                self.chvol[ch] = d2
            elif d1 == 121:
                self.chvol[ch] = 127
            elif d1 == 120:                                        # all sound off
                for v in self.voices:
                    if v.ch == ch:
                        v.fade(64)
            elif d1 == 123:                                        # all notes off: ends held (gate) notes only
                for v in self.voices:
                    if v.ch == ch and v.gate:
                        v.fade(self._rel_for(v.note))
            else:
                self.stats["ignored"] += 1
        else:
            self.stats["ignored"] += 1                             # program change, pitch bend, sysex ...: counted

    def _rel_for(self, note):
        s = self.table[note] if 0 <= note < 128 else None
        return s.rel if s else 64

    def _note_on(self, ch, note, vel):
        S = self.stats
        S["note_on"] += 1
        S["seen"][note] += 1
        S["chan_seen"][ch] += 1
        if ch not in self.channels:
            S["filtered"] += 1                                     # a channel the sampler is told to ignore (counted, not silent)
            return
        self.last_hit[note] = time.perf_counter()
        slot = self.table[note]
        if slot is None:
            S["unmapped"][note] += 1
            return
        layers = [L for L in slot.layers if L.vmin <= vel <= L.vmax]
        if not layers:                                             # velocity gap: use the nearest layer, and say so
            S["vel_fallback"] += 1
            layers = [min(slot.layers, key=lambda L: min(abs(vel - L.vmin), abs(vel - L.vmax)))]
        if slot.choke:
            for v in self.voices:
                if v.choke == slot.choke and not v.fade_len:
                    v.fade(slot.rel)
        gain = ((vel / 127.0) ** self.vel_curve) * (self.chvol[ch] / 127.0)
        cache = self.cache
        for L in layers:
            smp = cache.get(L.ref)
            if smp is None:
                st = cache.status.get(L.ref)
                if st is None or st[0] in ("pending", "on_demand"):
                    S["late_loads"] += 1                           # not in RAM yet: it is being loaded; this hit is late/lost
                S["missing"][note] += 1
                continue
            if L.start >= smp.data.shape[0]:
                continue
            if len(self.voices) >= self.polyphony:
                live = [v for v in self.voices if not v.fade_len]
                if live:
                    min(live, key=lambda v: v.born).fade(64)
                    S["stolen"] += 1
            self._born += 1
            self.voices.append(_Voice(smp.data, L.start, L.gl * gain, L.gr * gain, ch, note, slot.gate, slot.choke, self._born))

    def active_notes(self, window=0.25):
        now = time.perf_counter()
        return [n for n in range(128) if now - self.last_hit[n] < window]


# ============================== the sound output ==========================================================================================
class SamplerOut:
    """Drop-in output for DX-Arranger (like the other outputs): the style player calls send()/send_many() with raw MIDI bytes."""
    dry = False

    def __init__(self, core, block=256, device=None, lazy=False):
        self.core, self.rate, self.block = core, core.rate, int(block)
        self.device = device
        self.lazy = lazy
        self.lock = threading.Lock()
        self.pending = []
        self.error = ""
        self.stream = None
        self.dropped = 0
        self._prev = time.perf_counter()
        self._last_use = time.time()
        self._timer = None
        if not lazy:
            self.start()

    # ---- stream -------------------------------------------------------------------------------------------------------------------------
    def _find_device(self, sd):
        if not self.device:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and str(self.device).lower() in d["name"].lower():
                return i
        print(f"! audio output '{self.device}' not found; using the default.")
        return None

    def start(self):
        if self.stream is not None:
            return True
        try:
            import sounddevice as sd
            self._prev = time.perf_counter()
            self.stream = sd.OutputStream(samplerate=self.rate, blocksize=self.block, channels=2, dtype="float32",
                                          device=self._find_device(sd), callback=self._callback, latency="low")
            self.stream.start()
            self.error = ""
            return True
        except Exception as err:                                    # no device, busy device, missing package ...
            self.stream = None
            self.error = f"No audio output: {err}"
            print("! drum engine:", self.error)
            return False

    def stop(self):
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass

    def ensure(self):
        """Lazy mode (another synth is the main sound): open the stream on first use, close it after ~15 s of silence."""
        self._last_use = time.time()
        ok = self.stream is not None or self.start()
        if ok and self.lazy and self._timer is None:
            self._timer = threading.Timer(15.0, self._idle_check)
            self._timer.daemon = True
            self._timer.start()
        return ok

    def _idle_check(self):
        self._timer = None
        if self.lazy and self.stream is not None:
            if self.core.voices or time.time() - self._last_use < 12.0:
                self._timer = threading.Timer(5.0, self._idle_check)
                self._timer.daemon = True
                self._timer.start()
            else:
                self.stop()

    def _callback(self, outdata, frames, tinfo, status):
        now = time.perf_counter()
        prev, self._prev = self._prev, now
        with self.lock:
            batch, self.pending = self.pending, []
        dur = frames / self.rate
        ev = [(int(min(max(t - prev, 0.0), dur * 0.999) * self.rate), m[0], m[1], m[2] if len(m) > 2 else 0)
              for m, t in batch]
        ev.sort(key=lambda e: e[0])                                  # stable: events at one offset keep their order
        try:
            outdata[:] = self.core.process(frames, ev)
        except Exception as err:                                     # the audio thread must never die
            outdata.fill(0)
            if str(err) != self.error:
                self.error = str(err)
                print("! drum engine audio error:", err)

    # ---- the interface the engine uses (same as the other outputs) ---------------------------------------------------------------------
    def send(self, m):
        if len(m) < 2 or not (0x80 <= m[0] < 0xF0):
            self.core.stats["ignored"] += 1                           # sysex / realtime: not for a sample player (counted)
            return
        if self.stream is None and not self.ensure():
            self.dropped += 1
            return
        t = time.perf_counter()
        with self.lock:
            self.pending.append((bytes(m), t))

    def send_many(self, msgs):
        for m in msgs:
            self.send(m)

    def set_gain(self, pct):
        self.core.outgain = max(0, min(100, pct)) / 100.0
        if pct <= 0:
            self.core.panic()

    def close(self):
        if self.stream is None:
            return
        self.core.panic()                                           # fade everything out, then stop the stream
        time.sleep(self.block / self.rate * 3)
        self.stop()
