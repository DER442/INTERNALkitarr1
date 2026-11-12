"""Stage 5 - compatibility checker: which MIDI notes does a style use, and can the selected kit play them?

Status:
  COMPATIBLE    every note the style uses has at least one readable sample.
  PARTIAL       some used notes have no sample, or only a missing/unsupported/unreadable one.
  INCOMPATIBLE  the style cannot be read, no kit is loaded, or NONE of the used notes can be played.
                (My reading of the brief: a kit that can play nothing of the style is not "partial".)
The style is only read; velocities that fall in a gap between a kit's velocity layers are listed as warnings
(playback uses the nearest layer, see drummap.py).
"""
import os

from . import formats

DRUM_CHANNELS = (9, 10)           # Yamaha rhythm parts (1-based): channel 9 = sub rhythm, 10 = rhythm


def scan_style(path, channels=None):
    """Read a .STY/.PRS... file and collect the note-ons per section. channels: 1-based list, None = all 16.
    Returns dict(readable, error, name, sections={name: {'events': n, 'notes': {note: count}}},
                 notes={note: {'count', 'vels': set, 'channels': set, 'sections': set}}, total, note_offs)."""
    import sty_play as sp
    res = dict(readable=False, error="", name=os.path.splitext(os.path.basename(path))[0], path=path,
               sections={}, notes={}, total=0, note_offs=0, channels=sorted(channels) if channels else list(range(1, 17)))
    try:
        ppq, events = sp.load(path)
        secs, _ = sp.find_sections(events, ppq)
    except SystemExit as err:                       # sty_play.load() reports problems with sys.exit(message)
        res["error"] = str(err)
        return res
    except Exception as err:
        res["error"] = f"{err.__class__.__name__}: {err}"
        return res
    if not secs:
        res["error"] = "No sections (markers) found in that file."
        return res
    want = set(channels) if channels else None
    for name, (a, b) in secs.items():
        sec = res["sections"].setdefault(name, dict(events=0, notes={}))
        for t, k, i in events:
            if k != "chan" or not (a <= t < b):
                continue
            kind, chan, data = i
            if kind not in (0x80, 0x90) or len(data) < 2 or (want and chan not in want):
                continue
            if kind == 0x80 or data[1] == 0:        # Note On velocity 0 is a Note Off
                res["note_offs"] += 1
                continue
            note, vel = data[0], data[1]
            res["total"] += 1
            sec["events"] += 1
            sec["notes"][note] = sec["notes"].get(note, 0) + 1
            e = res["notes"].setdefault(note, dict(count=0, vels=set(), channels=set(), sections=set()))
            e["count"] += 1
            e["vels"].add(vel)
            e["channels"].add(chan)
            e["sections"].add(name)
    res["readable"] = True
    return res


def _layer_state(engine, ref):
    """('ok'|'missing'|'unsupported'|'unreadable', reason) for a layer's sample, without needing it in RAM."""
    st = engine.cache.status.get(ref)
    if st and st[0] in ("missing", "unsupported", "unreadable"):
        return st
    info = formats.probe(engine.resolve(ref), ref)
    if info.status == "ok":
        return ("ok", "")
    return (info.status, info.reason)


def check(engine, usage):
    kit = engine.kit
    out = dict(status="INCOMPATIBLE", reason="", style=usage["name"], channels=usage["channels"],
               kit=kit.name if kit else "", total_notes=usage["total"], unique_notes=len(usage["notes"]),
               assigned=0, unassigned=0, unassigned_notes=[], missing_samples=[], unsupported_formats=[],
               velocity_gaps=[], notes=[], sections=[], playable_notes=0)
    if not usage["readable"]:
        out["reason"] = "The style could not be read: " + usage["error"]
        return out
    out["sections"] = [dict(name=n, events=s["events"], notes=sorted(s["notes"])) for n, s in usage["sections"].items()]
    if kit is None:
        out["reason"] = "No drumkit is loaded."
        return out
    dm = kit.dm
    for note in sorted(usage["notes"]):
        u = usage["notes"][note]
        e = dm.notes[note]
        row = dict(note=note, name=e["name"], count=u["count"], sections=sorted(u["sections"]), state="ok", detail="")
        if not e["layers"]:
            row["state"] = "unassigned"
            out["unassigned"] += 1
            out["unassigned_notes"].append(note)
        else:
            out["assigned"] += 1
            good, bad = [], []
            for L in e["layers"]:
                state, why = _layer_state(engine, L["sample"])
                (good if state == "ok" else bad).append((L, state, why))
            for L, state, why in bad:
                rec = dict(note=note, name=e["name"], sample=L["sample"], reason=why)
                if state == "unsupported":
                    rec["format"] = os.path.splitext(formats.split_ref(L["sample"])[0])[1].lower()
                    out["unsupported_formats"].append(rec)
                else:
                    rec["state"] = state
                    out["missing_samples"].append(rec)
            if not good:
                row["state"] = bad[0][1] if bad[0][1] in ("missing", "unsupported", "unreadable") else "missing"
                row["detail"] = bad[0][2]
            else:
                gaps = [v for v in sorted(u["vels"]) if not any(L["vel_min"] <= v <= L["vel_max"] for L, _, _ in good)]
                if gaps:
                    out["velocity_gaps"].append(dict(note=note, name=e["name"], velocities=gaps[:20], count=len(gaps)))
        out["notes"].append(row)
    ok = sum(1 for r in out["notes"] if r["state"] == "ok")
    out["playable_notes"] = ok
    n = len(out["notes"])
    if n == 0:
        out["status"], out["reason"] = "COMPATIBLE", "The style has no note events on the checked channels, so nothing needs a sample."
    elif ok == n:
        out["status"], out["reason"] = "COMPATIBLE", f"All {n} notes used by the style have a playable sample."
    elif ok == 0:
        out["status"], out["reason"] = "INCOMPATIBLE", f"None of the {n} notes used by the style can be played with this kit."
    else:
        out["status"], out["reason"] = "PARTIAL", f"{n - ok} of {n} notes used by the style cannot be played with this kit."
    return out
