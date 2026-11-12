"""Stage 3 - the editable 128-note drum map (one JSON file per kit, DRUMS/DrumMaps/<kit_id>.json).

Every kit has all 128 note positions (0-127). A position with no layers is simply unassigned: it is still a
recognised note and the compatibility checker and the live statistics report it. General MIDI is only an
optional naming / filename-matching helper, never imposed.

Per note:   name, volume (0-2, 1 = unchanged), pan (-1..1), mode ("oneshot" = ignore Note Off, the drum default;
            "gate" = Note Off fades the sound out), release_ms (fade for gate mode and choke), choke (0 = none;
            notes sharing a number cut each other off, e.g. closed/open hi-hat), layers[].
Per layer:  sample (path relative to the kit folder, or to a registered sample folder, or absolute; SoundFont
            members look like 'bank.sf2#12'), vel_min, vel_max, volume (0-2), pan (-1..1), start_ms.
            Layers whose velocity ranges overlap play together (layering); non-overlapping ranges = velocity switching.
"""
import json
import os
import re

MAP_VERSION = 1

GM_NAMES = {
    27: "High Q", 28: "Slap", 29: "Scratch Push", 30: "Scratch Pull", 31: "Sticks", 32: "Square Click",
    33: "Metronome Click", 34: "Metronome Bell", 35: "Kick 2", 36: "Kick 1", 37: "Side Stick", 38: "Snare 1",
    39: "Hand Clap", 40: "Snare 2", 41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom",
    44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat", 47: "Low-Mid Tom", 48: "High-Mid Tom",
    49: "Crash Cymbal 1", 50: "High Tom", 51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell",
    54: "Tambourine", 55: "Splash Cymbal", 56: "Cowbell", 57: "Crash Cymbal 2", 58: "Vibraslap",
    59: "Ride Cymbal 2", 60: "High Bongo", 61: "Low Bongo", 62: "Mute High Conga", 63: "Open High Conga",
    64: "Low Conga", 65: "High Timbale", 66: "Low Timbale", 67: "High Agogo", 68: "Low Agogo", 69: "Cabasa",
    70: "Maracas", 71: "Short Whistle", 72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro", 75: "Claves",
    76: "High Wood Block", 77: "Low Wood Block", 78: "Mute Cuica", 79: "Open Cuica", 80: "Mute Triangle",
    81: "Open Triangle", 82: "Shaker", 83: "Jingle Bell", 84: "Bell Tree", 85: "Castanets", 86: "Mute Surdo",
    87: "Open Surdo",
}
GM_CHOKE = {42: 1, 44: 1, 46: 1}          # used only by the optional GM name template

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_label(n):
    return f"{n} ({NOTE_NAMES[n % 12]}{n // 12 - 2})"      # Yamaha convention: middle C = C3 = 60


def new_layer(sample, vel_min=1, vel_max=127, volume=1.0, pan=0.0, start_ms=0.0):
    return {"sample": str(sample), "vel_min": int(vel_min), "vel_max": int(vel_max),
            "volume": float(volume), "pan": float(pan), "start_ms": float(start_ms)}


def new_note(n):
    return {"name": "", "volume": 1.0, "pan": 0.0, "mode": "oneshot", "release_ms": 40.0, "choke": 0, "layers": []}


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class DrumMap:
    def __init__(self, kit_name="", kit_folder=""):
        self.kit_name, self.kit_folder = kit_name, kit_folder
        self.notes = {n: new_note(n) for n in range(128)}
        self.master_volume = 1.0
        self.dirty = False
        self.warnings = []                      # problems found while reading a map file (never silent)

    # ---- editing ------------------------------------------------------------------------------
    @staticmethod
    def check_note(n):
        n = int(n)
        if not 0 <= n <= 127:
            raise ValueError("MIDI note must be 0-127")
        return n

    def assign(self, note, sample, vel_min=1, vel_max=127, replace=True, layer=None, volume=1.0, pan=0.0):
        """Give a note a sample. replace=True: it becomes the note's only layer (or layer index `layer`)."""
        n = self.notes[self.check_note(note)]
        lo, hi = int(_clamp(vel_min, 1, 127, 1)), int(_clamp(vel_max, 1, 127, 127))
        if lo > hi:
            raise ValueError("vel_min is higher than vel_max")
        new = new_layer(sample, lo, hi, _clamp(volume, 0, 2, 1.0), _clamp(pan, -1, 1, 0.0))
        if layer is not None and 0 <= int(layer) < len(n["layers"]):
            n["layers"][int(layer)] = new
        elif replace:
            n["layers"] = [new]
        else:
            n["layers"].append(new)
        self.dirty = True

    def remove_layer(self, note, layer):
        n = self.notes[self.check_note(note)]
        if 0 <= int(layer) < len(n["layers"]):
            del n["layers"][int(layer)]
            self.dirty = True

    def clear(self, note):
        self.notes[self.check_note(note)]["layers"] = []
        self.dirty = True

    def set_note(self, note, **kw):
        n = self.notes[self.check_note(note)]
        if "name" in kw:
            n["name"] = str(kw["name"])[:60]
        if "volume" in kw:
            n["volume"] = _clamp(kw["volume"], 0, 2, 1.0)
        if "pan" in kw:
            n["pan"] = _clamp(kw["pan"], -1, 1, 0.0)
        if "mode" in kw and kw["mode"] in ("oneshot", "gate"):
            n["mode"] = kw["mode"]
        if "release_ms" in kw:
            n["release_ms"] = _clamp(kw["release_ms"], 2, 5000, 40.0)
        if "choke" in kw:
            n["choke"] = int(_clamp(kw["choke"], 0, 99, 0))
        self.dirty = True

    def set_layer(self, note, layer, **kw):
        ls = self.notes[self.check_note(note)]["layers"]
        if not 0 <= int(layer) < len(ls):
            raise ValueError("no such layer")
        L = ls[int(layer)]
        if "volume" in kw:
            L["volume"] = _clamp(kw["volume"], 0, 2, 1.0)
        if "pan" in kw:
            L["pan"] = _clamp(kw["pan"], -1, 1, 0.0)
        if "vel_min" in kw:
            L["vel_min"] = int(_clamp(kw["vel_min"], 1, 127, 1))
        if "vel_max" in kw:
            L["vel_max"] = int(_clamp(kw["vel_max"], 1, 127, 127))
        if "start_ms" in kw:
            L["start_ms"] = _clamp(kw["start_ms"], 0, 2000, 0.0)
        if L["vel_min"] > L["vel_max"]:
            L["vel_min"], L["vel_max"] = L["vel_max"], L["vel_min"]
        self.dirty = True

    def apply_gm_names(self, overwrite=False):
        """Optional template: fill in General MIDI instrument NAMES (and hi-hat choke) where a note has none."""
        for n, name in GM_NAMES.items():
            e = self.notes[n]
            if overwrite or not e["name"]:
                e["name"] = name
            if n in GM_CHOKE and not e["choke"]:
                e["choke"] = GM_CHOKE[n]
        self.dirty = True

    def assigned(self):
        return [n for n in range(128) if self.notes[n]["layers"]]

    # ---- JSON ----------------------------------------------------------------------------------
    def to_json(self):
        return {"version": MAP_VERSION, "kit_name": self.kit_name, "kit_folder": self.kit_folder,
                "master_volume": self.master_volume,
                "notes": {str(n): self.notes[n] for n in range(128)}}

    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, indent=1)
        os.replace(tmp, path)
        self.dirty = False

    @classmethod
    def load(cls, path, kit_name="", kit_folder=""):
        m = cls(kit_name, kit_folder)
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("notes"), dict):
            raise ValueError("not a drum map file (no 'notes' table)")
        m.kit_name = d.get("kit_name") or kit_name
        m.kit_folder = d.get("kit_folder") or kit_folder
        m.master_volume = _clamp(d.get("master_volume", 1.0), 0, 2, 1.0)
        for key, e in d["notes"].items():
            try:
                n = int(key)
                if not 0 <= n <= 127 or not isinstance(e, dict):
                    raise ValueError
            except ValueError:
                m.warnings.append(f"ignored invalid note entry '{key}' (notes must be 0-127)")
                continue
            t = m.notes[n]
            t["name"] = str(e.get("name", ""))[:60]
            t["volume"] = _clamp(e.get("volume", 1.0), 0, 2, 1.0)
            t["pan"] = _clamp(e.get("pan", 0.0), -1, 1, 0.0)
            t["mode"] = e.get("mode") if e.get("mode") in ("oneshot", "gate") else "oneshot"
            t["release_ms"] = _clamp(e.get("release_ms", 40.0), 2, 5000, 40.0)
            t["choke"] = int(_clamp(e.get("choke", 0), 0, 99, 0))
            for L in e.get("layers", []) or []:
                if not isinstance(L, dict) or not L.get("sample"):
                    m.warnings.append(f"note {n}: a layer without a sample was ignored")
                    continue
                lo = int(_clamp(L.get("vel_min", 1), 1, 127, 1))
                hi = int(_clamp(L.get("vel_max", 127), 1, 127, 127))
                t["layers"].append(new_layer(L["sample"], min(lo, hi), max(lo, hi), _clamp(L.get("volume", 1.0), 0, 2, 1.0),
                                             _clamp(L.get("pan", 0.0), -1, 1, 0.0), _clamp(L.get("start_ms", 0.0), 0, 2000, 0.0)))
        m.dirty = False
        return m


# ============================== filename -> note (a HEURISTIC helper, always editable) ====================
# Ordered: the first rule that matches wins. Written for common drum-sample naming.
_RULES = [
    (r"(open|\bo\b|\boh\b|ohh)[ _-]*(hi)?[ _-]*hat|hat[ _-]*open|hihat[ _-]*open|\bohh?\b", 46),
    (r"(pedal|foot)[ _-]*(hi)?[ _-]*hat|hat[ _-]*pedal", 44),
    (r"(closed|\bc\b|chh)[ _-]*(hi)?[ _-]*hat|hat[ _-]*closed|hihat|hi[ _-]*hat|\bhh\b|\bchh\b|\bhat", 42),
    (r"side[ _-]*stick|rim[ _-]*shot|\brim\b|cross[ _-]*stick", 37),
    (r"clap", 39),
    (r"snare|\bsn\b|\bsd\b", 38),
    (r"kick|\bbd\b|bass[ _-]*drum", 36),
    (r"floor[ _-]*tom|tom[ _-]*floor|low[ _-]*tom|tom[ _-]*(1|low)\b", 41),
    (r"(mid|medium)[ _-]*tom|tom[ _-]*(2|mid)\b", 47),
    (r"high[ _-]*tom|hi[ _-]*tom|tom[ _-]*(3|high)\b", 50),
    (r"\btom", 45),
    (r"crash", 49), (r"splash", 55), (r"china|chinese", 52),
    (r"ride[ _-]*bell", 53), (r"ride", 51),
    (r"tamb", 54), (r"cowbell|cow[ _-]*bell", 56), (r"cabasa", 69), (r"maraca", 70), (r"shaker", 82),
    (r"claves", 75), (r"wood[ _-]*block", 76), (r"conga", 63), (r"bongo", 60), (r"timbale", 65),
    (r"agogo", 67), (r"triangle", 81), (r"guiro", 73), (r"whistle", 71), (r"castanet", 85), (r"surdo", 87),
]


def guess_note(filename):
    """Note for a sample file name, or None. A leading/embedded explicit number wins ('36_kick.wav' -> 36),
    otherwise keywords ('Snare 03.wav' -> 38). A guess: the result is always shown and editable."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = re.match(r"^\s*(?:note|n)?[ _-]*(\d{1,3})(?=\D|$)", stem, re.I)
    if m and int(m.group(1)) <= 127:
        return int(m.group(1))
    low = stem.lower().replace(".", " ").replace("_", " ")   # "_" is a word character for \\b
    for pat, note in _RULES:
        if re.search(pat, low):
            return note
    return None


def auto_assign(dm, infos, kit_folder, overwrite=False):
    """Assign usable files to notes by name. Returns {'assigned': {note: ref}, 'extra': [refs sharing a note],
    'unmatched': [refs with no recognisable name]}. Existing assignments are kept unless overwrite=True."""
    done, extra, unmatched = {}, [], []
    for inf in infos:
        if inf.status != "ok":
            continue
        n = guess_note(inf.ref.rpartition("#")[0] if "#" in inf.ref else inf.ref)
        if n is None:
            unmatched.append(inf.ref)
            continue
        if n in done or (dm.notes[n]["layers"] and not overwrite):
            extra.append(inf.ref)
            continue
        rel = os.path.relpath(inf.ref, kit_folder) if _inside(inf.ref, kit_folder) else inf.ref
        dm.assign(n, rel)
        done[n] = rel
        if not dm.notes[n]["name"]:
            dm.notes[n]["name"] = GM_NAMES.get(n) or inf.name
    return {"assigned": done, "extra": extra, "unmatched": unmatched}


def _inside(path, folder):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:
        return False
