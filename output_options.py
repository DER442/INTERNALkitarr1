"""App-facing output option: the internal sampler or an open MIDI port.

This wrapper only persists the user's choice. The existing arranger output path remains
unchanged; apply the choice on the next app start so a live audio device is never swapped
out underneath the style player.
"""
import json
import os

from .manager import DrumEngine as _DrumEngine


class DrumEngine(_DrumEngine):
    def _app_config_path(self):
        return os.path.join(os.path.dirname(self.folders.root), "config.json")

    def _read_app_config(self):
        try:
            with open(self._app_config_path(), encoding="utf-8") as f:
                value = json.load(f)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def state(self):
        result = super().state()
        result["midi_output"] = "midiout" if self._read_app_config().get("synth", "sampler") == "midiout" else "internal"
        result["output_restart_required"] = False
        return result

    def command(self, body, ctx=None):
        if body.get("op") != "set_midi_output":
            return super().command(body, ctx)
        ctx = ctx or {}
        if not ctx.get("local"):
            return {"error": "Choose the MIDI output on the computer running DX-Arranger."}
        target = str(body.get("target", ""))
        if target not in ("internal", "midiout"):
            return {"error": "Choose Internal drum kits or MIDI port."}
        path = self._app_config_path()
        config = self._read_app_config()
        config["synth"] = "sampler" if target == "internal" else "midiout"
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, path)
        except OSError as err:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return {"error": f"Could not save MIDI output choice: {err}"}
        return {"ok": True, "target": target, "restart_required": True,
                "message": "Saved. Restart DX-Arranger, then load your beat and press Play."}
