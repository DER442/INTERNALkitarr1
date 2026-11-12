"""Stage 1 - the DRUMS folder tree, and the remembered kit / sample-folder choices.

Nothing is ever copied or moved: a kit or sample folder anywhere on disk is registered by its path.
All remembered state lives in DRUMS/drums_config.json (selection, folders, volumes); each kit's own
note map lives in DRUMS/DrumMaps/<kit_id>.json (see drummap.py).
"""
import hashlib
import json
import os
import re
import threading

STRUCTURE = [
    "DrumKits/PowerKit", "DrumKits/Yamaha", "DrumKits/Gospel", "DrumKits/Other",
    "Samples/WAV", "Samples/NCW", "Samples/Converted", "Samples/Other",
    "DrumMaps", "StyleCompatibility",
]

DEFAULT_SETTINGS = {
    "version": 1,
    "selected_kit": "",
    "kits": {},                 # kit_id -> {"name": str, "folder": str}   (user-added kits, any location)
    "sample_folders": [],       # extra folders of loose samples (any location)
    "master_volume": 1.0,       # 0..2
    "polyphony": 64,
    "max_ram_mb": 512,
    "velocity_curve": 1.0,
    "channels": list(range(1, 17)),   # MIDI channels (1-16) the sampler answers to
    "last_style": "",
}


def slug(text):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()
    return s or "kit"


def kit_id_for(folder):
    """Stable id from the folder path (so two kits both called 'Gospel' in different places never collide)."""
    ap = os.path.normcase(os.path.abspath(folder))
    return slug(os.path.basename(ap)) + "-" + hashlib.sha1(ap.encode("utf-8", "replace")).hexdigest()[:6]


class DrumFolders:
    def __init__(self, app_dir):
        self.root = os.path.join(app_dir, "DRUMS")
        self.config_path = os.path.join(self.root, "drums_config.json")
        self.lock = threading.RLock()
        self.created = self.ensure_structure()
        self.settings = self._load()

    # ---- folder tree ---------------------------------------------------------------
    def ensure_structure(self):
        created = []
        for rel in STRUCTURE:
            path = os.path.join(self.root, *rel.split("/"))
            if not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)
                created.append(path)
        return created

    @property
    def maps_dir(self):
        return os.path.join(self.root, "DrumMaps")

    @property
    def compat_dir(self):
        return os.path.join(self.root, "StyleCompatibility")

    # ---- remembered settings ---------------------------------------------------------
    def _load(self):
        s = json.loads(json.dumps(DEFAULT_SETTINGS))
        try:
            with open(self.config_path, encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                s.update({k: v for k, v in user.items() if k in DEFAULT_SETTINGS})
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as err:
            print(f"! DRUMS/drums_config.json could not be read ({err}); starting with defaults. "
                  "The unreadable file is kept as drums_config.json.bad")
            try:
                os.replace(self.config_path, self.config_path + ".bad")
            except OSError:
                pass
        return s

    def save(self):
        with self.lock:
            tmp = self.config_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.settings, f, indent=2)
                os.replace(tmp, self.config_path)          # atomic: a crash can never leave half a file
            except OSError as err:
                print(f"! could not save DRUMS/drums_config.json: {err}")

    # ---- kits ----------------------------------------------------------------------------
    def builtin_kits(self):
        """Every sub-folder of DRUMS/DrumKits is a kit (PowerKit, Yamaha, Gospel, Other, anything you add)."""
        out = {}
        base = os.path.join(self.root, "DrumKits")
        try:
            names = sorted(os.listdir(base), key=str.lower)
        except OSError:
            names = []
        for n in names:
            p = os.path.join(base, n)
            if os.path.isdir(p):
                out[kit_id_for(p)] = {"name": n, "folder": p, "builtin": True}
        return out

    def all_kits(self):
        kits = self.builtin_kits()
        with self.lock:
            for kid, k in self.settings["kits"].items():
                kits[kid] = {"name": k.get("name") or os.path.basename(k["folder"]),
                             "folder": k["folder"], "builtin": False}
        return kits

    def add_kit(self, folder, name=None):
        folder = os.path.abspath(str(folder).strip().strip('"'))
        if not os.path.isdir(folder):
            raise ValueError(f"Not a folder: {folder}")
        kid = kit_id_for(folder)
        if kid not in self.builtin_kits():
            with self.lock:
                self.settings["kits"][kid] = {"name": name or os.path.basename(folder) or folder, "folder": folder}
            self.save()
        return kid

    def remove_kit(self, kid):
        """Forgets the kit. The folder and its files are NOT touched (the map file is kept too)."""
        with self.lock:
            self.settings["kits"].pop(kid, None)
            if self.settings["selected_kit"] == kid:
                self.settings["selected_kit"] = ""
        self.save()

    # ---- loose sample folders ----------------------------------------------------------------
    def sample_folders(self):
        """DRUMS/Samples/* plus the folders the user registered. Returns [{'name','folder','builtin'}]."""
        out = []
        base = os.path.join(self.root, "Samples")
        for n in sorted(os.listdir(base), key=str.lower) if os.path.isdir(base) else []:
            p = os.path.join(base, n)
            if os.path.isdir(p):
                out.append({"name": "Samples/" + n, "folder": p, "builtin": True})
        with self.lock:
            for p in self.settings["sample_folders"]:
                out.append({"name": os.path.basename(p) or p, "folder": p, "builtin": False})
        return out

    def add_sample_folder(self, folder):
        folder = os.path.abspath(str(folder).strip().strip('"'))
        if not os.path.isdir(folder):
            raise ValueError(f"Not a folder: {folder}")
        with self.lock:
            if folder not in self.settings["sample_folders"]:
                self.settings["sample_folders"].append(folder)
        self.save()
        return folder

    def remove_sample_folder(self, folder):
        with self.lock:
            if folder in self.settings["sample_folders"]:
                self.settings["sample_folders"].remove(folder)
        self.save()
