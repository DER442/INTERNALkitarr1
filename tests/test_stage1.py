"""Stage 1 - folder system. Run:  python tests/test_stage1.py"""
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from drum_engine.folders import DrumFolders, STRUCTURE, kit_id_for

tmp = tempfile.mkdtemp()
try:
    f = DrumFolders(tmp)
    for rel in STRUCTURE:
        assert os.path.isdir(os.path.join(tmp, "DRUMS", *rel.split("/"))), rel
    assert len(f.created) == len(STRUCTURE)
    assert len(DrumFolders(tmp).created) == 0            # second start creates nothing
    names = sorted(k["name"] for k in f.builtin_kits().values())
    assert names == ["Gospel", "Other", "PowerKit", "Yamaha"], names

    ext = os.path.join(tmp, "elsewhere", "MyKit"); os.makedirs(ext)
    open(os.path.join(ext, "keep.wav"), "wb").write(b"x")
    kid = f.add_kit(ext)
    f.add_sample_folder(os.path.join(tmp, "elsewhere"))
    f.settings["selected_kit"] = kid; f.settings["master_volume"] = 0.8; f.save()
    g = DrumFolders(tmp)                                   # "restart": choices are remembered
    assert g.settings["selected_kit"] == kid and g.settings["master_volume"] == 0.8
    assert kid in g.all_kits() and g.all_kits()[kid]["folder"] == ext
    assert os.listdir(ext) == ["keep.wav"]                 # nothing copied or moved into/out of the kit folder
    try:
        g.add_kit(os.path.join(tmp, "nope")); raise SystemExit("should have refused a missing folder")
    except ValueError:
        pass
    g.remove_kit(kid)
    assert kid not in g.all_kits() and os.path.isfile(os.path.join(ext, "keep.wav"))   # forgetting never deletes files
    open(g.config_path, "w").write("{broken")              # corrupt config must not crash startup
    assert DrumFolders(tmp).settings["master_volume"] == 1.0
    print("STAGE 1 OK")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
