"""DX-Arranger drum engine exports.

The app-facing engine adds a small persistent choice for where style MIDI is rendered,
while the implementation remains in manager.py and stays independent of style playback.
"""


def __getattr__(name):
    if name == "DrumEngine":
        from .output_options import DrumEngine
        return DrumEngine
    raise AttributeError(name)
