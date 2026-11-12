DX ARRANGER  -  your panel + the style engine
==============================================

START
  1. Double-click start.bat   (or: open cmd here and run  python arranger_app.py)
  2. Your browser opens the panel. Wait for the bottom-left status line to say the engine is ready.
  3. Leave the black window open while you play. Ctrl+C in it closes everything.

FIRST SETUP (once)
  * Open config.json (made on first run) and check "fluid_exe" and "soundfont".
    If FluidSynth does not start, the app falls back to the Windows synth and tells you why;
    FluidSynth's own messages are in fluid_log.txt.
  * Copy your .STY files into the Styles\P01 ... P16 folders (one folder per P button),
    or right-click a P button on the panel (long-press on a touch screen) and choose any folder.

DRUMS ONLY
  The app now plays only the rhythm parts (both Yamaha drum channels, 9 and 10). Bass, chords and pads are
  never sent. config.json:  "drums_only": true,  "drums_include_sub": false = channel 10 alone.
  If beats still feel slightly loose, set "fluid_bufsize": 128 in config.json.
  No kick in some styles: run  python sty_play.py FILE "Main A" --notes --drums  to see the kit and notes; then
  set "drum_kit": 0 in config.json (forces the Standard kit) or try --kit=0 on the command line.
  Command line:  python sty_play.py beat "Main A" --drums   (--no9 = channel 10 only; --live for the keys)

PLAYING  
  P1..P16        show the styles in that folder on the LCD
  NAV arrows     move through the list          NAV-ENTER   load the highlighted style
  SY-1 / SY-2 / STOP    start / stop / stop
  V-A..V-D       Main A-D (plays its fill-in first if AFH is on). Press while a fill plays: the
                 new fill takes over from the same point in the bar.
  I-A..I-D       Intro (press before starting to arm it; while playing it starts at the next bar)
  BR-1           Break          EN-1..EN-3   Ending A-C
  AFH            Auto Fill-in on/off  (my assumption for "A.FH" - tell me if it means something else)
  Tempo          use the panel's tempo screen (NAV2 -/+ are 1 BPM per press); heard at once, mid-bar
  PERC pads      drum hits (which note each pad plays: "perc" in config.json)
  MASTER + NAV2  master volume (FluidSynth only)
  VOL-1..6 + NAV2   part volumes: 1 Rhythm, 2 Rhythm main, 3 Bass, 4 Chord 1, 5 Chord 2, 6 Pad/Phrase
                 (my assumption: change "vol_groups" in config.json)
  A button greyed out means the style has no such section.

IMPORT A BEAT (works from a phone too)
  STYLES > INBUILT > pick an empty FILE slot > ENTER > ENTER (import) > choose the .STY file.
  It is sent to the PC, saved in Styles\Imported and loaded at once. Press the start button to play.
  Later: pick the same slot and press ENTER to load it again.
  R-1..R-4  tap = recall, hold or right-click = save (style, main, tempo, A.FH, volumes)
  TAP = tap tempo   MUTE = silence   EFX = reverb+chorus on/off   MENU = LCD main menu

NOT CONNECTED YET
  C.M, Multi Pad chord match, SYNC REC, chord following (the style plays as
  written), SFF chord rules, audio-link pads. 

EFFECTS (MENU > EFFECTS, or tap the EFFECTS icon)
  GRAPHIC EQ, EQ3-M, EZ EQ, STUDIO REVERB, SPRING REVERB, BL DRIVE. Each shows ON/OFF: NAV-ENTER (or a tap) switches it,
  "ALL EFFECTS OFF" at the bottom clears everything, NAV-LEFT goes back. All start OFF and your choice is remembered.
  Honest note: the sound engine (FluidSynth) has reverb but no equalizer or distortion, so:
    STUDIO / SPRING REVERB   real reverb settings (spring = short, bright, narrow; it has no spring "boing").
    GRAPHIC EQ / EQ3-M / EZ EQ   balance the drum kit by frequency range (kick = low ... cymbals and shakers = air).
    BL DRIVE                 harder, denser drum hits (soft hits lifted), not audio distortion.
  Amounts in config.json: fx_geq [kick, low, mid, high, air] dB, fx_eq3m [low, mid, high] dB, fx_ezeq (dB tilt, - = darker),
  fx_drive (0-1), fx_studio / fx_spring [room, damping, width, level].

CHANGING STYLE WHILE PLAYING
  Pick another style while one is playing and it does NOT stop: at the next bar line the new style takes over
  (same Main/fill type), at the SAME tempo, on the same beat. Pressing sections (A-D, fills, break, ending) never stops it either.

SOUND (config.json)
  "shaker_level": 0.6  soft shakers (maracas, cabasa, shaker). 0.4 = very soft, 1 = as written.
  "high_polish": 0.7   hats, shakers, tambourine, ride get a played feel (beat strongest, 16ths softest). 0 = off.
  "fluid_rate": 48000, "fluid_interp": 7   cleaner highs. "fluid_reverb": [room, damping, width, level] = soft-tailed air
  (raise level for more room; null = FluidSynth default). "fluid_gain" is the master level.
  "soundfont_extra": ["C:\\path\\Drums.sf2"]  loads more SoundFonts ON TOP of the main one; a better drum-kit .sf2 here replaces
  the drums only. "fluid_polyphony": 512 keeps long cymbal/shaker tails from being cut.
  The biggest change of all is the SoundFont itself: a richer .sf2 in "soundfont" changes the whole sound.

MULTI PADS (P-1..P-4 under PADS)
  LOAD:  MENU > STYLES > MULTIPAD opens the Multipad folder on the LCD.
         First entry "+ ADD FILES" sends files from this PC or phone into the folder.
         P-1..P-4 choose the target pad while the list is open, NAV-ENTER loads the highlighted file onto it
         (the target then moves to the next pad). A .pad/.mid file with several markers fills pads 1-4 at once.
  PLAY:  press a pad: it LOOPS until you press it again or press STOP (STOP stops the beat and every pad).
         For play-once pads set "multipad_repeat": false in config.json. While a style plays, a pad starts on the style's next beat and
         follows every tempo change. With the style stopped it starts at once at the panel tempo.
  RHYTHM: percussion in a pad (shakers, tambourine...) plays on the DRUM channel: channel 10 always, channel 9 too in a
         .pad file, and any channel using a drum kit bank. Only the notes are sent, so a pad never changes the style's
         drum kit. Shakers follow "shaker_level". A long silent tail after the last note is trimmed to whole bars so
         the pad loops on the beat. When you load a pad the console prints what it found (drums, instruments, loop length).
  FILES: .pad .mid .midi = MIDI phrases (played by the synth, exact tempo follow).
         .wav .mp3 = audio samples (Windows only; started on the beat but NOT time-stretched).
         Folder: "multipad_folder" in config.json (default Multipad). 
         Which pad holds which file is remembered in pads.json.
  NOT BUILT: chord match (pads do not follow chords yet). The .pad layout is not published by Yamaha: if one of
         your .pad files does not load, send it and the reader will be adapted.

TOOLS (read-only, in the tools folder)
  sty_inspect.py   file layout        sty_casm_decode.py   channel rules
  sty_scan.py      check a whole folder of styles -> compat_report.csv
  pad_inspect.py   look inside a .pad file
Your .STY files are only ever read, never changed.

USING AN iPHONE / iPAD (remote control)
  1. Put the PC and the phone on the same Wi-Fi.
  2. Start with start-phone.bat (instead of start.bat). Allow Windows Firewall for PRIVATE networks.
  3. The black window prints  Phone: http://192.168.x.x:8765/  and a PIN.
  4. Open that address in Safari, type the PIN. Share > Add to Home Screen for a full-screen app.
  The sound still plays on the PC. Changing a P folder must be done on the PC.
  Anyone on your Wi-Fi who knows the PIN can control it: use start.bat when you do not need the phone.
