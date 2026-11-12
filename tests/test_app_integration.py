"""The real arranger_app: Engine + HTTP handler + /drums page + /api/drums, with synth = "sampler" and NO audio device."""
import os, sys, json, tempfile, shutil, threading, time, urllib.request
from helpers import *
import arranger_app as app
from http.server import ThreadingHTTPServer

tmp = tempfile.mkdtemp()
try:
    app.APP_DIR = tmp
    app.CONFIG_PATH = os.path.join(tmp, "config.json")
    cfg = json.loads(json.dumps(app.DEFAULT_CFG)); cfg["synth"] = "sampler"; cfg["html_file"] = os.path.join(tmp, "p.html")
    open(cfg["html_file"], "w").write("<html><head></head><body>panel</body></html>")
    cfg["multipad_folder"] = os.path.join(tmp, "Multipad"); os.makedirs(cfg["multipad_folder"])
    app.CFG = cfg; app.PIN = ""
    kit = os.path.join(tmp, "kit"); os.makedirs(kit)
    for fn in ("36_kick.wav", "snare.wav", "hat.wav"): make_wav(os.path.join(kit, fn), click(2000, 0.3))
    E = app.Engine(cfg); app.ENGINE = E
    for _ in range(100):                                    # engine thread initialises the synth
        if E.st["synth"] != "starting" or E.out: break
        time.sleep(0.05)
    time.sleep(0.3)
    # no audio device here: the sampler must be refused, the app must fall back and SAY so
    assert E.out is not None and "No audio output" in (E.st["error"] + E.st["message"]), (E.st["error"], E.st["message"])   # sampler refused -> app fell back and said why
    print("fallback reported:", (E.st["error"] or E.st["message"])[:100])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    def post(body):
        r = urllib.request.Request(f"http://127.0.0.1:{port}/api/drums", json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r).read())
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/drums").read().decode()
    assert "DRUM ENGINE" in page and "Internal drumkit" in page         # the compact setup page (the mapping editor is API-only)
    assert "panel" in urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()       # the old panel still serves
    s = post({"op": "state"}); assert s["kit"] is None and len(s["kits"]) == 4 and s["formats"]["supported"] == [".ncw", ".sf2", ".wav"]
    r = post({"op": "add_kit", "path": kit}); assert r["ok"] and r["state"]["kit"]["assigned"] == 3, r
    n = post({"op": "notes"})["notes"]; assert len(n) == 128 and n[36]["state"] in ("assigned", "loading")
    assert post({"op": "pool"})["pool"][0]["status"] == "ok"
    assert post({"op": "assign", "note": 127, "sample": os.path.join(kit, "hat.wav")})["ok"]
    assert post({"op": "set_note", "note": 127, "volume": 0.5, "name": "Edge"})["ok"]
    assert "error" in post({"op": "assign", "note": 128, "sample": "x.wav"})              # rejected, app still fine
    assert "error" in post({"op": "assign", "sample": "x.wav"})                            # malformed -> error, not a crash
    assert "error" in post({"op": "nonsense"})
    t = post({"op": "test_note", "note": 36}); assert t["ok"] is False and "audio" in t["error"].lower()   # reported, no crash
    sty = make_sty(os.path.join(tmp, "s.sty"), [("Main A", 1, [(0, 10, 36, 100, 50), (480, 10, 99, 100, 50)])])
    c = post({"op": "compat", "style": sty}); assert c["status"] == "PARTIAL" and c["unassigned_notes"] == [99], c
    assert "error" in post({"op": "compat", "style": os.path.join(tmp, "none.sty")})
    assert post({"op": "setting", "key": "channels", "value": [10]})["settings"]["channels"] == [10]
    assert "error" in post({"op": "setting", "key": "channels", "value": []})
    # restart: the app remembers the kit and its map (async restore)
    E2 = app.DrumEngine(tmp, cfg, restore="async") if hasattr(app, "DrumEngine") else None
    from drum_engine import DrumEngine
    D = DrumEngine(tmp, cfg, restore="async")
    for _ in range(100):
        if D.kit: break
        time.sleep(0.05)
    assert D.kit and 127 in D.kit.dm.assigned() and D.core.channels == frozenset({9})
    # sampler mode turns the app's velocity humanising off
    sty2 = make_sty(os.path.join(tmp, "h.sty"), [("Main A", 1, [(0, 10, 42, 100, 50), (120, 10, 42, 100, 50)])])
    pkg = E._prepare(sty2); vel = [m[2] for _, m in pkg["get"]("Main A")[0] if m[0] == 0x99]; assert vel == [100, 100], vel
    cfg["synth"] = "windows"; pkg = E._prepare(sty2); vel2 = [m[2] for _, m in pkg["get"]("Main A")[0] if m[0] == 0x99]; assert vel2 != [100, 100]   # unchanged behaviour elsewhere
    srv.shutdown(); E.shutdown()
    print("APP INTEGRATION OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
