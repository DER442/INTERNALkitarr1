"""Stage 5 - kits through the manager + the compatibility checker (COMPATIBLE / PARTIAL / INCOMPATIBLE)."""
import os, tempfile, shutil, json
from helpers import *
from drum_engine.manager import DrumEngine

tmp = tempfile.mkdtemp()
P = lambda *a: os.path.join(tmp, *a)
try:
    app = P("app"); os.makedirs(app)
    kit = P("elsewhere", "MyKit"); os.makedirs(kit)
    for fn in ("36_kick.wav", "Snare 01.wav", "closed hat.wav", "OpenHat.wav", "crash.wav", "weird name.wav"):
        make_wav(os.path.join(kit, fn), click(2000, 0.3))
    open(os.path.join(kit, "loops.nki"), "wb").write(b"abc"); open(os.path.join(kit, "bad.wav"), "wb").write(b"nope")
    before = sorted(os.listdir(kit))

    # --- load a kit that lives anywhere; nothing copied; auto-assignment by name, always editable -----------------------------------
    E = DrumEngine(app, {})
    kid = E.folders.add_kit(kit); assert E.load_kit(kid), E.error
    assert sorted(os.listdir(kit)) == before                                   # originals untouched, nothing copied in
    dm = E.kit.dm
    assert dm.assigned() == [36, 38, 42, 46, 49], dm.assigned()
    pool = {os.path.basename(p["path"]): p["status"] for p in E.pool()}
    assert pool["loops.nki"] == "unsupported" and pool["bad.wav"] == "unreadable" and pool["weird name.wav"] == "ok"   # all listed, honestly
    assert os.path.isfile(P("app", "DRUMS", "DrumMaps", kid + ".json")) and "First load" in E.messages[0]
    E.cache.wait_idle(); rows = E.note_rows(); assert len(rows) == 128 and rows[36]["state"] == "assigned" and rows[37]["state"] == "unassigned"
    # edits are saved and survive a restart together with the selection
    E.assign(37, os.path.join(kit, "weird name.wav")); E.edit("set_note", 37, name="Rim", volume=0.5)
    E.cache.wait_idle(); assert E.note_rows()[37]["state"] == "assigned"
    E2 = DrumEngine(app, {}); assert E2.kit and E2.kit.id == kid and E2.kit.dm.notes[37]["name"] == "Rim" and 37 in E2.kit.dm.assigned()
    assert E2.kit.dm.notes[37]["layers"][0]["sample"] == "weird name.wav"      # stored relative to the kit: the kit folder can move
    E.reload(); assert 37 in E.kit.dm.assigned()
    # a kit that cannot load leaves the working kit alone
    other = P("gone"); os.makedirs(other); kid2 = E.folders.add_kit(other); shutil.rmtree(other)
    assert not E.load_kit(kid2) and "not available" in E.error and E.kit.id == kid and E.core.table[36] is not None
    # a damaged map is set aside, not trusted and not silently overwritten
    mp = P("app", "DRUMS", "DrumMaps", kid + ".json"); open(mp, "w").write("{oops")
    assert E.load_kit(kid) and os.path.isfile(mp + ".bad") and "unreadable" in E.messages[0]

    # --- the style: Main A uses 36, 38, 42, 46, 49 (all assigned) + 99 (not) + 0 ; ch 9/10 drums + a bass line --------------------------------------------
    S = 480
    notes = [(0, 10, 36, 100, 50), (S, 10, 38, 100, 50), (S * 2, 10, 42, 100, 50), (S * 3, 10, 46, 100, 50), (S * 4, 10, 49, 100, 50),
             (S * 5, 9, 99, 90, 50), (S * 6, 10, 0, 90, 50), (0, 4, 55, 100, 50)]
    sty = make_sty(P("a.sty"), [("Main A", 2, notes), ("Fill In AA", 1, [(0, 10, 38, 50, 20), (S, 10, 41, 50, 20)])])
    r = E.check_style(sty)
    assert r["status"] == "PARTIAL" and r["total_notes"] == 9 and r["unique_notes"] == 8, r      # bass ignored: drum channels only
    assert sorted(r["unassigned_notes"]) == [0, 41, 99] and r["unassigned"] == 3 and r["assigned"] == 5 and r["playable_notes"] == 5, r
    assert [s["name"] for s in r["sections"]] == ["Main A", "Fill In AA"] and not r["missing_samples"] and not r["unsupported_formats"]
    # assign the missing notes right from the checker ("assign missing samples") -> re-check
    for n in (0, 41, 99): E.assign(n, os.path.join(kit, "crash.wav"))
    r = E.check_style(sty); assert r["status"] == "COMPATIBLE" and r["unassigned"] == 0 and r["assigned"] == 8, r
    # all 16 channels checked on request: the bass note 55 on channel 4 becomes a required note
    r = E.check_style(sty, channels=list(range(1, 17))); assert r["status"] == "PARTIAL" and r["unassigned_notes"] == [55] and r["unique_notes"] == 9
    # missing sample file / unsupported format / unreadable file are told apart
    E.assign(36, "vanished.wav"); E.assign(38, "loops.nki"); E.assign(42, "bad.wav"); E.cache.wait_idle()
    r = E.check_style(sty)
    assert r["status"] == "PARTIAL" and {m["note"] for m in r["missing_samples"]} == {36, 42} and [u["note"] for u in r["unsupported_formats"]] == [38]
    assert r["unsupported_formats"][0]["format"] == ".nki" and {x["note"]: x["state"] for x in r["notes"]}[36] == "missing"
    rows = E.note_rows(); assert rows[36]["state"] == "missing" and rows[38]["state"] == "missing" and rows[46]["state"] == "assigned"
    # velocity gap warning (the kit only has soft layers where the style hits hard)
    E.assign(36, "36_kick.wav", vel_min=1, vel_max=40); E.assign(38, "Snare 01.wav"); E.assign(42, "closed hat.wav")
    r = E.check_style(sty); assert r["status"] == "COMPATIBLE" and [g["note"] for g in r["velocity_gaps"]] == [36], r["velocity_gaps"]
    # nothing playable at all -> INCOMPATIBLE; unreadable style -> INCOMPATIBLE; no kit -> INCOMPATIBLE
    for n in range(128): E.edit("clear", n)
    assert E.check_style(sty)["status"] == "INCOMPATIBLE"
    open(P("junk.sty"), "wb").write(b"not a style"); r = E.check_style(P("junk.sty")); assert r["status"] == "INCOMPATIBLE" and "could not be read" in r["reason"], r
    r = E.check_style(P("missing.sty")); assert r["status"] == "INCOMPATIBLE"
    E3 = DrumEngine(P("app2"), {}); r = E3.check_style(sty); assert r["status"] == "INCOMPATIBLE" and "No drumkit" in r["reason"]
    # a style with no drum events is compatible with anything (nothing needs a sample)
    r = E.check_style(make_sty(P("nodrums.sty"), [("Main A", 1, [(0, 4, 50, 90, 50)])])); assert r["status"] == "COMPATIBLE" and r["total_notes"] == 0
    # test note / preview with no audio device must report, not crash
    t = E.test_note(36, 100, 10); assert t["ok"] is False and "audio" in t["error"].lower(), t
    assert E.test_note(200)["ok"] is False
    p = E.preview(os.path.join(kit, "loops.nki")); assert p["ok"] is False and "Unsupported" in p["error"]
    p = E.preview(os.path.join(kit, "nowhere.wav")); assert p["ok"] is False
    print("STAGE 5 OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
