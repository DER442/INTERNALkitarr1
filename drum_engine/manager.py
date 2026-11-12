"""DrumEngine - the one object the app talks to: kits, maps, sample pool, sampler, previews, compatibility.

Failure policy: nothing in here raises into the app. A kit that cannot be loaded leaves the previous kit playing and
sets .error; a bad sample is reported per sample; a missing audio device is reported in state()['audio'].
"""
import json
import os
import threading
import time

from . import compat, formats
from .drummap import DrumMap, GM_NAMES, auto_assign, note_label, _inside
from .folders import DrumFolders
from .sampler import SampleCache, SamplerCore, SamplerOut, compile_map


class Kit:
    def __init__(self, kid, name, folder, dm, map_path, pool):
        self.id, self.name, self.folder, self.dm, self.map_path, self.pool = kid, name, folder, dm, map_path, pool


class DrumEngine:
    def __init__(self, app_dir, cfg=None, log=print, restore="sync"):
        cfg = cfg or {}
        self.log = log
        self.lock = threading.RLock()
        self.folders = DrumFolders(app_dir)
        s = self.folders.settings
        self.rate = int(cfg.get("sampler_rate") or 44100)
        self.block = int(cfg.get("sampler_block", 256))
        self.device = cfg.get("sampler_device")
        self.cache = SampleCache(self.rate, s["max_ram_mb"], resolver=self.resolve)
        self.core = SamplerCore(self.rate, s["polyphony"], self.cache)
        self.core.master = float(s["master_volume"])
        self.core.vel_curve = float(s["velocity_curve"])
        self.core.set_channels(s["channels"])
        self.audio = None
        self.main_output = False
        self.kit = None
        self.error = ""
        self.messages = []
        self._pool_cache = {}
        sel = s.get("selected_kit")
        if sel and restore == "async":                       # a big sample library must not delay the app's start
            threading.Thread(target=self.load_kit, args=(sel,), daemon=True, name="drum-kit-restore").start()
        elif sel:
            self.load_kit(sel)
        elif self.folders.all_kits():                        # first run: nothing chosen yet, nothing assumed
            self.messages.append("No drumkit selected yet: choose one under DRUM ENGINE > Selected Drumkit.")

    # ============================== output ============================================================================
    def make_output(self, main=True):
        """The SamplerOut. main=True: it is the app's sound engine (raises if there is no audio device so the app
        can fall back). main=False: a lazy stream used only for previews / test notes while another synth is main."""
        with self.lock:
            if self.audio is None:
                self.audio = SamplerOut(self.core, self.block, self.device, lazy=not main)
            elif main and self.audio.lazy:
                self.audio.lazy = False
            self.main_output = self.main_output or main
            if main and not self.audio.start():
                raise RuntimeError(self.audio.error or "no audio output")
            return self.audio

    def _out(self):
        a = self.audio or self.make_output(main=False)
        return a

    # ============================== kits ================================================================================
    def resolve(self, ref):
        """Absolute path for a sample reference (the '#n' of a SoundFont member is kept)."""
        real, sep, frag = ref.rpartition("#") if ("#" in ref and ref.rpartition("#")[2].isdigit()) else (ref, "", "")
        tail = ("#" + frag) if sep else ""
        if os.path.isabs(real):
            return real + tail
        kit = self.kit
        if kit:
            p = os.path.join(kit.folder, real)
            if os.path.exists(p):
                return p + tail
        for f in self.folders.sample_folders():
            p = os.path.join(f["folder"], real)
            if os.path.exists(p):
                return p + tail
        return (os.path.join(kit.folder, real) if kit else real) + tail

    def _map_path(self, kid):
        return os.path.join(self.folders.maps_dir, kid + ".json")

    def load_kit(self, kid):
        """Select and load a kit. Returns True on success. On failure the previous kit keeps playing."""
        with self.lock:
            try:
                info = self.folders.all_kits().get(kid)
                if not info:
                    raise ValueError("That drumkit is not registered.")
                folder = info["folder"]
                if not os.path.isdir(folder):
                    raise ValueError(f"The kit folder is not available: {folder}")
                pool = formats.scan(folder)
                mp = self._map_path(kid)
                note = ""
                if os.path.isfile(mp):
                    try:
                        dm = DrumMap.load(mp, info["name"], folder)
                        if dm.warnings:
                            note = " Map warnings: " + "; ".join(dm.warnings[:3])
                    except (OSError, ValueError) as err:       # a damaged map is kept aside, never overwritten silently
                        try:
                            os.replace(mp, mp + ".bad")
                        except OSError:
                            pass
                        dm = DrumMap(info["name"], folder)
                        note = f" The saved map was unreadable ({err}); it was kept as {os.path.basename(mp)}.bad and a new map was started."
                        auto_assign(dm, pool, folder)
                else:
                    dm = DrumMap(info["name"], folder)
                    res = auto_assign(dm, pool, folder)
                    note = (f" First load: {len(res['assigned'])} samples were assigned by file name"
                            f" ({len(res['unmatched'])} not recognised) - check the mapping.")
                    try:
                        dm.save(mp)
                    except OSError as err:
                        note += f" (map could not be saved: {err})"
                kit = Kit(kid, info["name"], folder, dm, mp, pool)
                self.cache.reset()
                self.kit = kit
                self.core.reset_stats()
                self._recompile(preload=True)
                self.folders.settings["selected_kit"] = kid
                self.folders.save()
                bad = sum(1 for p in pool if p.status != "ok")
                self.error = ""
                self.messages = [f"Kit '{kit.name}': {len(dm.assigned())} notes assigned, {len(pool)} files found"
                                 + (f", {bad} unsupported or unreadable" if bad else "") + "." + note]
                return True
            except Exception as err:                              # includes ValueError above: never propagate into the app
                self.error = f"Could not load the drumkit: {err}"
                self.log("! drum engine:", self.error)
                return False

    def reload(self):
        """Re-read the folder, the map file and every sample from disk."""
        kid = self.kit.id if self.kit else self.folders.settings.get("selected_kit")
        self._pool_cache.clear()
        return bool(kid) and self.load_kit(kid)

    def _recompile(self, preload=False):
        kit = self.kit
        if not kit:
            self.core.set_table([None] * 128)
            return
        self.core.set_table(compile_map(kit.dm, self.rate))
        refs = []
        for n in range(128):
            for L in kit.dm.notes[n]["layers"]:
                if L["sample"] not in self.cache.status and L["sample"] not in self.cache.samples and L["sample"] not in refs:
                    refs.append(L["sample"])
        self.cache.preload(refs)

    def _edited(self):
        """After any map edit: rebuild the playing table at once, and save the file (atomic)."""
        self._recompile()
        kit = self.kit
        try:
            kit.dm.save(kit.map_path)
        except OSError as err:
            self.error = f"Could not save the drum map: {err}"

    def save(self):
        if not self.kit:
            raise ValueError("No drumkit is loaded.")
        self.kit.dm.save(self.kit.map_path)
        self.folders.save()
        return self.kit.map_path

    # ============================== sample pool ======================================================================
    def pool(self, rescan=False):
        """Every audio file in the kit folder and the sample folders. Unsupported / unreadable files are included."""
        sources = []
        if self.kit:
            sources.append(("Kit: " + self.kit.name, self.kit.folder))
        sources += [(f["name"], f["folder"]) for f in self.folders.sample_folders()]
        out, seen = [], set()
        for label, folder in sources:
            if rescan or folder not in self._pool_cache:
                self._pool_cache[folder] = formats.scan(folder)
            for i in self._pool_cache[folder]:
                if i.ref in seen:
                    continue
                seen.add(i.ref)
                d = i.to_json()
                d["source"] = label
                d["path"] = i.ref
                out.append(d)
        return out

    # ============================== editing ==========================================================================
    def _ref_for(self, path):
        kit = self.kit
        if kit and not os.path.isabs(path):
            return path
        real, sep, frag = path.rpartition("#") if ("#" in path and path.rpartition("#")[2].isdigit()) else (path, "", "")
        if kit and _inside(real, kit.folder):
            return os.path.relpath(real, kit.folder).replace("\\", "/") + (sep + frag)
        return path

    def assign(self, note, sample, **kw):
        if not self.kit:
            raise ValueError("Load a drumkit first.")
        with self.lock:
            self.kit.dm.assign(note, self._ref_for(str(sample)), vel_min=kw.get("vel_min", 1), vel_max=kw.get("vel_max", 127),
                               replace=kw.get("replace", True), layer=kw.get("layer"),
                               volume=kw.get("volume", 1.0), pan=kw.get("pan", 0.0))
            self._edited()

    def edit(self, fn, *a, **kw):
        if not self.kit:
            raise ValueError("Load a drumkit first.")
        with self.lock:
            getattr(self.kit.dm, fn)(*a, **kw)
            self._edited()

    def auto_assign(self, overwrite=False):
        if not self.kit:
            raise ValueError("Load a drumkit first.")
        with self.lock:
            pool = formats.scan(self.kit.folder)
            res = auto_assign(self.kit.dm, pool, self.kit.folder, overwrite=overwrite)
            self._edited()
            return res

    def note_rows(self):
        """All 128 notes for the mapping view. state: assigned | unassigned | missing (a sample is absent/unusable) | loading"""
        rows = []
        kit = self.kit
        for n in range(128):
            if not kit:
                rows.append(dict(note=n, label=note_label(n), state="unassigned", name="", layers=[], volume=1.0, pan=0.0,
                                 mode="oneshot", choke=0, gm=GM_NAMES.get(n, "")))
                continue
            e = kit.dm.notes[n]
            layers, state = [], "unassigned"
            if e["layers"]:
                state = "assigned"
                for L in e["layers"]:
                    st = self.cache.status.get(L["sample"], ("pending", ""))
                    layers.append(dict(L, status=st[0], reason=st[1]))
                    if st[0] in ("missing", "unsupported", "unreadable"):
                        state = "missing"
                    elif st[0] == "pending" and state != "missing":
                        state = "loading"
            rows.append(dict(note=n, label=note_label(n), state=state, name=e["name"], layers=layers, volume=e["volume"],
                             pan=e["pan"], mode=e["mode"], choke=e["choke"], release_ms=e["release_ms"], gm=GM_NAMES.get(n, "")))
        return rows

    # ============================== playing from the UI ===============================================================
    def preview(self, path):
        """Play a file straight from disk (any pool file), not through the map."""
        try:
            smp = formats.load(self.resolve(path), self.rate)
        except formats.UnsupportedFormat as err:
            return dict(ok=False, error="Unsupported format: " + str(err))
        except FileNotFoundError:
            return dict(ok=False, error="File not found.")
        except Exception as err:
            return dict(ok=False, error=f"Cannot read that sample: {err}")
        out = self._out()
        if out.stream is None and not out.ensure():
            return dict(ok=False, error=out.error or "No audio output.")
        out._last_use = time.time()
        self.core.preview(smp)
        return dict(ok=True, seconds=round(smp.frames / self.rate, 2))

    def test_note(self, note, vel=100, channel=10, ms=400):
        """Send a Note On (and later a Note Off) through exactly the path a style's MIDI takes."""
        note, vel, ch = int(note), int(vel), int(channel)
        if not (0 <= note <= 127 and 1 <= vel <= 127 and 1 <= ch <= 16):
            return dict(ok=False, error="note 0-127, velocity 1-127, channel 1-16")
        out = self._out()
        if out.stream is None and not out.ensure():
            return dict(ok=False, error=out.error or "No audio output.")
        out.send(bytes([0x90 | (ch - 1), note, vel]))
        t = threading.Timer(max(0.05, ms / 1000.0), out.send, [bytes([0x80 | (ch - 1), note, 0])])
        t.daemon = True
        t.start()
        s = self.core.table[note]
        return dict(ok=True, assigned=bool(s), note=note)

    # ============================== settings ===================================================================================
    def set_setting(self, key, value):
        s = self.folders.settings
        if key == "master_volume":
            s[key] = self.core.master = max(0.0, min(2.0, float(value)))
        elif key == "polyphony":
            s[key] = self.core.polyphony = max(4, min(512, int(value)))
        elif key == "velocity_curve":
            s[key] = self.core.vel_curve = max(0.3, min(3.0, float(value)))
        elif key == "max_ram_mb":
            s[key] = max(16, min(8192, int(value)))
            self.cache.max_bytes = s[key] * 1024 * 1024
        elif key == "channels":
            chans = sorted({int(c) for c in value if 1 <= int(c) <= 16})
            if not chans:
                raise ValueError("Pick at least one MIDI channel.")
            s[key] = chans
            self.core.set_channels(chans)
        else:
            raise ValueError("unknown setting")
        self.folders.save()

    # ============================== compatibility ================================================================================
    def check_style(self, style_path, channels=None):
        if channels is None:
            channels = list(compat.DRUM_CHANNELS)
        usage = compat.scan_style(style_path, channels or None)
        self.cache.wait_idle(5.0)                                  # let pending sample loads finish before judging
        res = compat.check(self, usage)
        res["path"] = style_path
        self.folders.settings["last_style"] = style_path
        return res

    # ============================== state for the page ===================================================================================
    def state(self):
        s = self.folders.settings
        kits = self.folders.all_kits()
        a = self.audio
        st = self.core.stats
        return dict(
            root=self.folders.root, kits=[dict(id=k, **v) for k, v in kits.items()],
            selected=self.kit.id if self.kit else "", kit=dict(name=self.kit.name, folder=self.kit.folder, map=self.kit.map_path,
                                                                assigned=len(self.kit.dm.assigned())) if self.kit else None,
            sample_folders=self.folders.sample_folders(), formats=dict(supported=formats.supported_exts(), unsupported=sorted(formats.KNOWN_UNSUPPORTED)),
            settings=dict(master_volume=s["master_volume"], polyphony=s["polyphony"], velocity_curve=s["velocity_curve"],
                          max_ram_mb=s["max_ram_mb"], channels=s["channels"]),
            audio=dict(running=bool(a and a.stream), main=self.main_output, error=(a.error if a else ""), rate=self.rate, block=self.block,
                       dropped_no_audio=a.dropped if a else 0),
            cache=dict(resident=len(self.cache.samples), mb=round(self.cache.bytes / 1048576, 1), loading=not self.cache.idle(),
                       problems=[dict(ref=r, state=v[0], reason=v[1]) for r, v in self.cache.status.items() if v[0] in ("missing", "unsupported", "unreadable")][:50]),
            stats=dict(note_on=st["note_on"], note_off=st["note_off"], stolen=st["stolen"], late_loads=st["late_loads"], ignored=st["ignored"],
                       vel_fallback=st["vel_fallback"], filtered=st["filtered"], voices=len(self.core.voices), max_voices=st["max_voices"],
                       peak=round(st["peak"], 3), clipped_blocks=st["clipped_blocks"],
                       unmapped=[dict(note=n, count=c) for n, c in enumerate(st["unmapped"]) if c],
                       missing=[dict(note=n, count=c) for n, c in enumerate(st["missing"]) if c]),
            error=self.error, messages=self.messages)

    def activity(self):
        return dict(active=self.core.active_notes(), voices=len(self.core.voices))

    def shutdown(self):
        try:
            if self.audio:
                self.audio.close()
        except Exception:
            pass

    # ============================== one entry point for the page ======================================================================
    def command(self, body, ctx=None):
        """body: {'op': ..., ...}. ctx: {'local': bool (request comes from this PC), 'pick_folder': fn(title)->path,
        'current_style': path, 'use_as_main': fn()}.  Always returns a dict; problems come back as {'error': text}."""
        ctx = ctx or {}
        op = body.get("op")
        try:
            if op == "state":
                return self.state()
            if op == "activity":
                d = self.activity()
                d["stats"] = self.state()["stats"] if body.get("stats") else None
                return d
            if op == "notes":
                return dict(notes=self.note_rows())
            if op == "pool":
                return dict(pool=self.pool(bool(body.get("rescan"))))
            if op in ("add_kit", "add_sample_folder"):
                path = str(body.get("path") or "").strip()
                if not path:
                    if not ctx.get("local") or not ctx.get("pick_folder"):
                        return dict(error="Type the folder path (the folder window opens on the PC only).")
                    path = ctx["pick_folder"]("Choose the drumkit folder" if op == "add_kit" else "Choose the sample folder")
                    if not path:
                        return dict(ok=False)
                if op == "add_sample_folder":
                    self.folders.add_sample_folder(path)
                    self._pool_cache.clear()
                    return dict(ok=True, state=self.state())
                kid = self.folders.add_kit(path)
                ok = self.load_kit(kid)
                return dict(ok=ok, error=self.error if not ok else "", state=self.state())
            if op == "select_kit":
                ok = self.load_kit(str(body.get("id")))
                return dict(ok=ok, error=self.error if not ok else "", state=self.state())
            if op == "remove_kit":
                kid = str(body.get("id"))
                self.folders.remove_kit(kid)
                if self.kit and self.kit.id == kid:
                    self.kit = None
                    self.cache.reset()
                    self._recompile()
                return dict(ok=True, state=self.state())
            if op == "remove_sample_folder":
                self.folders.remove_sample_folder(str(body.get("path")))
                self._pool_cache.clear()
                return dict(ok=True, state=self.state())
            if op == "reload":
                ok = self.reload()
                return dict(ok=ok, error=self.error if not ok else "", state=self.state())
            if op == "save":
                return dict(ok=True, path=self.save())
            if op == "assign":
                self.assign(int(body["note"]), str(body["sample"]), vel_min=body.get("vel_min", 1), vel_max=body.get("vel_max", 127),
                            replace=bool(body.get("replace", True)), layer=body.get("layer"),
                            volume=body.get("volume", 1.0), pan=body.get("pan", 0.0))
                return dict(ok=True)
            if op == "clear":
                self.edit("clear", int(body["note"]))
                return dict(ok=True)
            if op == "remove_layer":
                self.edit("remove_layer", int(body["note"]), int(body["layer"]))
                return dict(ok=True)
            if op == "set_note":
                kw = {k: body[k] for k in ("name", "volume", "pan", "mode", "release_ms", "choke") if k in body}
                self.edit("set_note", int(body["note"]), **kw)
                return dict(ok=True)
            if op == "set_layer":
                kw = {k: body[k] for k in ("volume", "pan", "vel_min", "vel_max", "start_ms") if k in body}
                self.edit("set_layer", int(body["note"]), int(body["layer"]), **kw)
                return dict(ok=True)
            if op == "auto_assign":
                return dict(ok=True, result=self.auto_assign(bool(body.get("overwrite"))))
            if op == "gm_names":
                self.edit("apply_gm_names", bool(body.get("overwrite")))
                return dict(ok=True)
            if op == "preview":
                return self.preview(str(body.get("path")))
            if op == "test_note":
                return self.test_note(body.get("note", 36), body.get("vel", 100), body.get("channel", 10), body.get("ms", 400))
            if op == "setting":
                self.set_setting(str(body.get("key")), body.get("value"))
                return dict(ok=True, settings=self.state()["settings"])
            if op == "panic":
                self.core.panic()
                return dict(ok=True)
            if op == "reset_stats":
                self.core.reset_stats()
                return dict(ok=True)
            if op == "compat":
                style = str(body.get("style") or ctx.get("current_style") or "")
                if not style:
                    return dict(error="Load a style first, or choose one to check.")
                if not os.path.isfile(style):
                    return dict(error=f"Style file not found: {style}")
                chans = None if body.get("all_channels") else list(compat.DRUM_CHANNELS)
                return self.check_style(style, chans if chans else list(range(1, 17)))
            if op == "use_as_main":
                if not ctx.get("local") or not ctx.get("use_as_main"):
                    return dict(error="Change the sound engine on the PC.")
                return dict(ok=True, message=ctx["use_as_main"]())
            return dict(error=f"unknown operation: {op}")
        except (ValueError, KeyError, TypeError) as err:
            return dict(error=str(err) if isinstance(err, ValueError) else f"bad request: {err!r}")
        except Exception as err:                       # the page must never take the app down
            self.log("! drum engine:", op, err)
            return dict(error=f"{err.__class__.__name__}: {err}")
