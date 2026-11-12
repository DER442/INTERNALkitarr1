"""effects.py - the EFFECTS menu: GRAPHIC EQ, EQ3-M, EZ EQ, STUDIO REVERB, SPRING REVERB, BL DRIVE.

What each one really does (FluidSynth, the sound engine, has reverb and chorus but NO equalizer or distortion):
  STUDIO REVERB / SPRING REVERB  real: they set FluidSynth's reverb (room size, damping, width, level). Spring is a
                                 short, bright, narrow setting that imitates a spring tank; it has no real spring "boing".
  GRAPHIC EQ / EQ3-M / EZ EQ     a mixing EQ for the drums: every drum instrument belongs to a frequency band
                                 (kick = low ... cymbals and shakers = air) and the band's gain is applied to the hits of
                                 that band. It re-balances the kit by frequency range; it does not filter the audio.
  BL DRIVE                       "drive" for drums: harder, denser hits (soft hits are lifted, loud ones stay loud),
                                 not distortion of the audio.
All of them start OFF, each has its own switch, and ALL EFFECTS OFF clears everything. Amounts are in config.json.
"""
import json
import os

FX = [("geq", "GRAPHIC EQ"), ("eq3m", "EQ3-M"), ("ezeq", "EZ EQ"),
      ("studio", "STUDIO REVERB"), ("spring", "SPRING REVERB"), ("drive", "BL DRIVE")]
IDS = [i for i, _ in FX]
REVERBS = ("studio", "spring")          # only one reverb at a time

# 0 kick/sub, 1 low (toms, congas), 2 mid (snare, rim, clap), 3 high (hats, tambourine), 4 air (cymbals, shakers)
BAND_OF = {}
for _band, _notes in enumerate((
        (35, 36),
        (41, 43, 45, 47, 48, 50, 60, 61, 62, 63, 64, 65, 66),
        (37, 38, 39, 40, 56, 67, 68, 76, 77, 78, 79),
        (42, 44, 46, 53, 54, 73, 74, 75),
        (49, 51, 52, 55, 57, 58, 59, 69, 70, 71, 72, 80, 81, 82, 83))):
    for _n in _notes:
        BAND_OF[_n] = _band


def band_of(note):
    if note in BAND_OF:
        return BAND_OF[note]
    return 0 if note <= 36 else 1 if note <= 50 else 2 if note <= 58 else 3 if note <= 72 else 4


DEFAULTS = {
    "fx_geq": [2, 0, -1, 1, 2],        # dB for: kick, low, mid, high, air
    "fx_eq3m": [2, 0, 2],              # dB for: low, mid, high
    "fx_ezeq": 3,                      # dB tilt: + = brighter (low down, high up), - = darker
    "fx_drive": 0.6,                   # 0..1
    "fx_studio": [0.75, 0.35, 1.0, 0.6],   # room, damping, width, level
    "fx_spring": [0.30, 0.10, 0.40, 0.70],
}


class Effects:
    def __init__(self, cfg, state_path):
        self.cfg, self.path = cfg, state_path
        self.on = {i: False for i in IDS}
        try:
            with open(state_path, encoding="utf-8") as f:
                for i in json.load(f).get("on", []):
                    if i in self.on:
                        self.on[i] = True
        except (OSError, ValueError):
            pass
        for a in REVERBS[1:]:                                 # a damaged file must not leave both reverbs on
            if self.on[REVERBS[0]] and self.on[a]:
                self.on[a] = False

    def _p(self, key):
        v = self.cfg.get(key)
        return DEFAULTS[key] if v is None else v

    def toggle(self, ident):
        if ident == "alloff":
            self.on = {i: False for i in IDS}
        elif ident in self.on:
            self.on[ident] = not self.on[ident]
            if self.on[ident] and ident in REVERBS:           # studio and spring exclude each other
                for other in REVERBS:
                    if other != ident:
                        self.on[other] = False
        self._save()
        return dict(self.on)

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"on": [i for i in IDS if self.on[i]]}, f)
        except OSError:
            pass

    def reverb(self):
        """(room, damping, width, level) for the reverb effect that is on, or None = the normal setting."""
        for i in REVERBS:
            if self.on[i]:
                return tuple(float(x) for x in self._p("fx_" + i))
        return None

    def band_db(self):
        """Total dB per band (5 values) from the EQs that are on."""
        db = [0.0] * 5
        if self.on["geq"]:
            for b, v in enumerate(self._p("fx_geq")[:5]):
                db[b] += float(v)
        if self.on["eq3m"]:
            lo, mid, hi = (float(x) for x in self._p("fx_eq3m")[:3])
            for b, v in enumerate((lo, lo, mid, hi, hi)):
                db[b] += v
        if self.on["ezeq"]:
            t = float(self._p("fx_ezeq"))
            for b, v in enumerate((-t, -t / 2, 0.0, t / 2, t)):
                db[b] += v
        return db

    def shape(self):
        """A function (note, velocity) -> velocity for drum hits, or None when no EQ/drive is on.
        Velocity maps to level at about 40*log10(v/127) dB in a SoundFont, so dB -> velocity factor = 10^(dB/40)."""
        db = self.band_db()
        drive = float(self._p("fx_drive")) if self.on["drive"] else 0.0
        if not any(db) and not drive:
            return None
        factor = [10 ** (db[band_of(n)] / 40.0) for n in range(128)]
        gamma = 1.0 - 0.45 * max(0.0, min(1.0, drive))

        def f(note, vel):
            v = vel / 127.0
            if drive:
                v = v ** gamma
            return max(1, min(127, int(round(127 * v * factor[note & 127]))))
        return f
