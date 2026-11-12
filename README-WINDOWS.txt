DX ARRANGER FOR WINDOWS (no Python needed)
===========================================
START:   double-click Start-DX-Arranger.bat   (or DX-Arranger.exe)
PHONE:   double-click Start-for-phone.bat -> it prints an address and a PIN for your phone/tablet.
         Allow Windows Firewall for PRIVATE networks when asked.

FIRST TIME
 * Windows may say "Windows protected your PC" (the program is not code-signed):
   click More info > Run anyway.
 * FluidSynth and a SoundFont are needed for the good sound. Open config.json (created on first
   run) and set "fluid_exe" and "soundfont" to your own files. Without them the Windows
   built-in synth is used.
 * Put .STY files in the Styles\P01..P16 folders, or import them from the panel.

Everything else works as described in README.txt.

SOUND (set "synth" in config.json)
 "sampler"  (default) the built-in drum sampler: MENU > DRUM ENGINE, load a kit folder of WAV / NCW / SF2 samples.
            See DRUM-ENGINE-README.txt. If no audio device can be opened the app falls back to the Windows synth and says so.
 "midiout"  send the drum MIDI out of a MIDI port, e.g. to a drum plugin's standalone program:
            1. Install loopMIDI (free) and create one port (default name "loopMIDI Port").
            2. Open the plugin's standalone program, or any VST host, and set its MIDI input to that port.
            3. In config.json set:  "synth": "midiout"   and   "midi_out_name": "loopMIDI"
               (the start-up window lists every MIDI output; use any part of the name you want).
            4. Start DX Arranger. EFX, reverb and master volume are then the plugin's job; MUTE still silences it.
 "windows"  the Windows built-in synth.   "null"  no sound.
