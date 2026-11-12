DX-ARRANGER - DRUM ENGINE QUICK START
=====================================

Simple workflow
---------------
1. Open MENU > DRUM ENGINE (or http://127.0.0.1:8765/drums).
2. In "MIDI drum output", choose Internal drum kits or External VST and save. Restart DX-Arranger after changing this choice.
3. For Internal drum kits, load a kit folder containing supported WAV or SF2 files. Filename patterns such as 36_kick.wav and snare.wav are mapped automatically when recognizable. The page shows the number of mapped MIDI notes.
4. Return to the usual arranger panel, load your beat/style, and press Play. MIDI drum notes are looked up by note number in the selected kit's map.
5. An optional extra sample folder adds files to the sample browser, but does not automatically change an existing kit map. Samples must be mapped to the MIDI note numbers used by the beat.

Troubleshooting
---------------
- No internal sound: confirm a kit is selected, the displayed mapped-note count is not zero, the WAV/SF2 files are readable, and restart after selecting Internal drum kits.
- If a beat uses notes without a sample assignment, those notes are silent in the internal sampler. The original full mapping editor and compatibility API remain available in the engine implementation, but this compact setup page intentionally exposes only the basic workflow.
- The sound device must be available to the app. A failed sampler audio-device start is reported by the app and should not be mistaken for a broken MIDI map.
- WAV and NCW are supported, and SF2 support is limited to extracting raw samples (presets/zones are not interpreted).
  NCW files are decoded inside the app (drum_engine/ncw.py), so no converter has to be installed. The NCW layout was
  written from a community description and has only been tested against files made by this project's own test encoder:
  if a real .ncw is listed as "unreadable", the reason names what did not match - please keep that file for debugging.
  To convert one to WAV yourself:  python ncw2wav.py file.ncw
- NKI, NKM, SF3, MP3 and other listed formats do not play directly in this build. Convert only using software you are licensed to use.

Architecture
------------
The sampler remains a separate MIDI output implementation. Yamaha style parsing/playback is not modified. The destination choice is saved into config.json and applied on the next application start so an audio output is never hot-swapped while a beat is playing.
