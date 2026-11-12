/* bridge.js - connects dxarrangernew1.html to the Python engine (arranger_app.py).
 * Loaded automatically by the app; your HTML file itself is never changed.
 *
 * Button ids (data-id) it controls:
 *   G-1..G-16        P1..P16 folder shortcuts: tap = list that folder's styles on the LCD,
 *                    right-click or long-press = choose which folder the button opens
 *   NAV-UP/DOWN/LEFT/RIGHT move in the file list, NAV-ENTER loads the highlighted style
 *   I-A..I-D         Intro   (armed while stopped, plays from the next bar while running)
 *   V-A..V-D         Main A-D (with its fill-in, if AFH is on)
 *   BR-1             Break (Fill In BA)
 *   EN-1..EN-3       Ending A-C
 *   SY-1 / SY-2 / STOP   start / stop / stop
 *   AFH              Auto Fill-in on/off (my assumption for "A.FH" - tell me if it means something else)
 *   PERC-1..6, PERC2-1..6   percussion pads (notes set in config.json)
 *   MASTER + NAV2 -/+       master volume
 *   #bpm tempo box   tempo
 * VOL-1..6 = part volumes (Rhythm sub, Rhythm, Bass, Chord 1, Chord 2, Pad, Phrase: see vol_groups in config.json).
 * P-1..P-4 = Multi Pads (MENU > STYLES > MULTIPAD loads files into them).
 * Not connected yet: C.M.
 * While the LCD is on the CREATE page, the panel's own beat maker works as before.
 */
(function () {
  'use strict';

  const SECTION = {
    'I-A': ['intro', 'A'], 'I-B': ['intro', 'B'], 'I-C': ['intro', 'C'], 'I-D': ['intro', 'D'],
    'V-A': ['main', 'A'], 'V-B': ['main', 'B'], 'V-C': ['main', 'C'], 'V-D': ['main', 'D'],
    'BR-1': ['break'], 'EN-1': ['ending', 'A'], 'EN-2': ['ending', 'B'], 'EN-3': ['ending', 'C'],
    'SY-1': ['start'], 'SY-2': ['stop'], 'STOP': ['stop'], 'AFH': ['autofill']
  };
  const NEEDS_SECTION = {   // which style section a button needs (to grey it out when missing)
    'I-A': 'Intro A', 'I-B': 'Intro B', 'I-C': 'Intro C', 'I-D': 'Intro D',
    'V-A': 'Main A', 'V-B': 'Main B', 'V-C': 'Main C', 'V-D': 'Main D',
    'BR-1': 'Fill In BA', 'EN-1': 'Ending A', 'EN-2': 'Ending B', 'EN-3': 'Ending C'
  };
  const LAMPS = Object.keys(NEEDS_SECTION).concat(['SY-1', 'AFH']);

  let st = { loaded: false };
  let bridgeSlot = null;      // which P button the file list belongs to
  let slotFiles = [];         // real file names in that folder
  let titleText = '';
  let lastRev = -1;
  let fromServer = false;
  let longPressed = false;
  let pressTimer = null;
  let toastText = '', toastUntil = 0;
  let lastPadError = '';
  let padMode = false;        // the LCD file list is showing the MULTIPAD folder

  async function api(path, body) {
    try {
      const opt = body === undefined ? undefined :
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      const r = await fetch(path, opt);
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  const inCreate = () => typeof lcdCreate !== 'undefined' && lcdCreate.classList.contains('visible');
  const $id = id => document.querySelector('[data-id="' + id + '"]');

  // ---- small status line (bottom-left) ------------------------------------
  const badge = document.createElement('div');
  badge.style.cssText = 'position:fixed;left:8px;bottom:6px;z-index:99999;font:12px/1.3 Consolas,monospace;' +
    'color:#9fe;background:rgba(0,0,0,.65);padding:3px 8px;border-radius:4px;max-width:70vw;pointer-events:none';
  document.body.appendChild(badge);

  function toast(text, ms) { toastText = text; toastUntil = Date.now() + (ms || 3500); renderBadge(); }

  function renderBadge() {
    let t;
    if (Date.now() < toastUntil) t = toastText;
    else if (!st || st.synth === undefined) t = 'ENGINE: not connected (is arranger_app.py running?)';
    else if (st.error) t = 'ERROR: ' + st.error;
    else if (st.loaded) t = st.name.toUpperCase() + '  |  ' + (st.running ? (st.section || '').toUpperCase() : 'STOPPED') +
      '  |  ' + Math.round(st.bpm) + ' BPM  |  ' + st.synth.toUpperCase();
    else t = st.message || 'ENGINE READY';
    badge.textContent = t;
    badge.style.color = (st && st.error) ? '#f99' : '#9fe';
  }

  // ---- LCD file list ------------------------------------------------------
  function applyTitle() {
    if ((!bridgeSlot && !padMode) || typeof lcdFiles === 'undefined') return;
    const s = lcdFiles.querySelector('.lcd-files-status');
    if (s) s.textContent = titleText;
  }

  const origRender = window.renderFileBrowser;
  if (origRender) {
    window.renderFileBrowser = function () {
      origRender.apply(this, arguments);
      if ((bridgeSlot || padMode) && fileType === 'user') applyTitle();
    };
  }
  const origRoot = window.showRootMenu;
  if (origRoot) {
    window.showRootMenu = function () { bridgeSlot = null; padMode = false; return origRoot.apply(this, arguments); };
  }
  const folderInput = document.getElementById('userFolderInput');
  if (folderInput) folderInput.addEventListener('change', () => { bridgeSlot = null; padMode = false; });

  async function openSlot(slot) {
    const r = await api('/api/folder?slot=' + slot);
    if (!r || r.error) { toast('Could not read folder P' + slot); return; }
    bridgeSlot = slot;
    padMode = false;
    slotFiles = r.files;
    titleText = 'P' + slot + '  ' + r.folder_name.toUpperCase();
    userFiles = r.labels.slice();
    openFileBrowser('user', 0);
    if (!r.files.length) {
      lcdFiles.innerHTML = '<div class="lcd-files-status">' + titleText + ' - EMPTY</div>';
      toast('Put .STY files in: ' + r.path, 7000);
    } else {
      applyTitle();
    }
  }

  async function chooseFolder(slot) {
    toast('Choose the folder for P' + slot + ' in the window that opened...', 8000);
    const r = await api('/api/setfolder', { slot: slot });
    if (r && r.ok) { toast('P' + slot + ' now opens: ' + r.path, 6000); openSlot(slot); }
    else toast('Folder not changed.');
  }

  // ---- clicks -------------------------------------------------------------
  // Capture phase: when a style is loaded, the engine buttons are handled here and the panel's
  // built-in beat maker does not also run.
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-id]');
    if (!el) return;
    const id = el.dataset.id;
    if (/^G-\d+$/.test(id) && longPressed) {
      longPressed = false; ev.stopImmediatePropagation(); ev.preventDefault(); return;
    }
    if (inCreate()) return;
    const cmd = SECTION[id];
    if (!cmd) return;
    if (id === 'AFH') {                        // works with or without a style
      ev.stopImmediatePropagation(); ev.preventDefault();
      api('/api/cmd', { op: 'autofill' }); return;
    }
    if (!st.loaded) return;                    // no style yet: the panel's own behaviour
    ev.stopImmediatePropagation(); ev.preventDefault();
    if (NEEDS_SECTION[id] && st.available && st.available.indexOf(NEEDS_SECTION[id]) < 0) {
      toast('This style has no ' + NEEDS_SECTION[id]); return;
    }
    api('/api/cmd', { op: cmd[0], arg: cmd[1] });
  }, true);

  // P1..P16: open that folder (runs after the panel's own handler, so ours wins)
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-id^="G-"]');
    if (!el || inCreate()) return;
    const slot = Number(el.dataset.id.slice(2));
    if (slot >= 1 && slot <= 16) openSlot(slot);
  });

  // ENTER loads the highlighted style: from a P folder, or an INBUILT slot you imported earlier
  const imported = {};                         // INBUILT slot number -> file name stored on the PC
  document.addEventListener('click', async ev => {
    if (!ev.target.closest('[data-id="NAV-ENTER"]')) return;
    if (typeof lcdFiles === 'undefined' || !lcdFiles.classList.contains('visible')) return;
    if (bridgeSlot && fileType === 'user') {
      const name = slotFiles[filePage * filesPerPage + fileRow * 2 + fileColumn];
      if (!name) return;
      toast('Loading ' + name + ' ...', 4000);
      const r = await api('/api/load', { slot: bridgeSlot, name: name });
      if (!r || r.error) toast('Could not load ' + name);
    } else if (fileType === 'inbuilt') {
      const file = imported[filePage * filesPerPage + fileRow * 2 + fileColumn + 1];
      if (!file) return;                       // empty slot: the panel asks to import
      toast('Loading ' + file + ' ...', 4000);
      const r = await api('/api/load', { slot: 0, name: file });
      if (!r || r.error) toast('Could not load ' + file);
    }
  });

  // A style chosen in the panel's import box (or from a phone) is sent to the PC and loaded
  const importInput = document.getElementById('styleImportInput');
  if (importInput) {
    importInput.addEventListener('change', async () => {
      const file = importInput.files[0];
      const slot = Number(importInput.dataset.slot);
      if (!file) return;
      toast('Sending ' + file.name + ' to the engine ...', 5000);
      let r = null;
      try {
        r = await (await fetch('/api/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file })).json();
      } catch (e) { /* handled below */ }
      if (r && r.ok) { imported[slot] = r.file; toast('Imported ' + r.file + '. Press the start button.', 6000); }
      else toast('Import failed: ' + ((r && r.error) || 'engine not reachable'), 6000);
    }, true);                                  // capture: read the file before the panel clears the input
  }

  // right-click / long-press on a P button = choose its folder
  document.addEventListener('contextmenu', ev => {
    const el = ev.target.closest('[data-id^="G-"]');
    if (!el) return;
    ev.preventDefault();
    chooseFolder(Number(el.dataset.id.slice(2)));
  });
  document.addEventListener('pointerdown', ev => {
    const g = ev.target.closest('[data-id^="G-"]');
    if (g && ev.pointerType !== 'mouse') {
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => { longPressed = true; chooseFolder(Number(g.dataset.id.slice(2))); }, 800);
    }
    const p = ev.target.closest('[data-id^="PERC"]');          // percussion pads: play on touch
    if (p && /^PERC2?-\d$/.test(p.dataset.id) && !inCreate()) {
      api('/api/cmd', { op: 'perc', arg: p.dataset.id });
      p.classList.add('active'); setTimeout(() => p.classList.remove('active'), 140);
    }
  }, true);
  ['pointerup', 'pointercancel'].forEach(n => document.addEventListener(n, () => clearTimeout(pressTimer), true));

  // ---- EFFECTS menu -----------------------------------------------------------
  // MENU > EFFECTS: each effect shows ON / OFF, NAV-ENTER (or a tap) switches it; the last entry switches all off.
  const FX_ITEMS = [['geq', 'GRAPHIC EQ'], ['eq3m', 'EQ3-M'], ['ezeq', 'EZ EQ'],
                    ['studio', 'STUDIO REVERB'], ['spring', 'SPRING REVERB'], ['drive', 'BL DRIVE']];
  if (typeof menuPages !== 'undefined' && !Object.getOwnPropertyDescriptor(menuPages, 'effects')) {
    Object.defineProperty(menuPages, 'effects', {
      configurable: true, enumerable: true,
      get() {
        const fx = (typeof st !== 'undefined' && st && st.fx) || {};
        return FX_ITEMS.map(([id, label]) => label +
          (fx[id] ? ' <span class="act">ON &#10003;</span>' : ' <span style="opacity:.55">OFF</span>'))
          .concat(['ALL EFFECTS OFF']);
      }
    });
  }
  let prevMenuPage = '';                                      // NAV-LEFT from EFFECTS returns with EFFECTS selected
  document.addEventListener('click', ev => { if (ev.target.closest('[data-id="NAV-LEFT"]')) prevMenuPage = menuPage; }, true);
  document.addEventListener('click', ev => {
    if (ev.target.closest('[data-id="NAV-LEFT"]') && prevMenuPage === 'effects' && menuPage === 'root') {
      menuIndex = 2; renderLcdMenu();
    }
  });
  async function toggleFx(i) {
    const item = FX_ITEMS[i];
    const r = await api('/api/cmd', { op: 'fx', arg: item ? item[0] : 'alloff' });
    if (!r || !r.fx) { toast('Engine not reachable', 3000); return; }
    st.fx = r.fx;
    renderLcdMenu();
    toast(item ? item[1] + (r.fx[item[0]] ? ' ON' : ' OFF') : 'ALL EFFECTS OFF', 2500);
  }

  // ---- MULTIPAD ---------------------------------------------------------------
  // MENU > STYLES > MULTIPAD lists the Multipad folder on the LCD. First entry = add files from this device.
  // P-1..P-4 pick the target pad while the list is open (otherwise they play the pads); ENTER loads the highlighted file.
  const PAD_ADD = '+ ADD FILES';
  let padFiles = [], padTarget = 1, padNames = ['', '', '', ''];
  const inPad = () => padMode && typeof lcdFiles !== 'undefined' && lcdFiles.classList.contains('visible');
  const padTitle = () => 'MULTIPAD  >PAD ' + padTarget + (padNames[padTarget - 1] ? ' (' + padNames[padTarget - 1] + ')' : ' (EMPTY)') +
    '  P1-P4 CHOOSE, ENTER LOADS';

  if (typeof menuPages !== 'undefined') {                 // add MULTIPAD as the 3rd entry of the STYLES page
    const d = Object.getOwnPropertyDescriptor(menuPages, 'styles');
    if (d && d.get) Object.defineProperty(menuPages, 'styles', {
      configurable: true, enumerable: true,
      get() { return d.get.call(menuPages).concat(['MULTIPAD']); }
    });
  }
  const origOpenMenu = window.openSelectedMenu;
  if (origOpenMenu) {
    window.openSelectedMenu = function () {
      if (menuPage === 'styles' && menuIndex === 2) { openPadBrowser(); return; }
      if (menuPage === 'root' && menuIndex === 2) { menuPage = 'effects'; menuIndex = 0; renderLcdMenu(); return; }
      if (menuPage === 'effects') { toggleFx(menuIndex); return; }
      return origOpenMenu.apply(this, arguments);
    };
  }

  async function openPadBrowser(keepTarget) {
    const r = await api('/api/padfolder');
    if (!r || r.error) { toast('Could not read the Multipad folder'); return; }
    bridgeSlot = null; padMode = true;
    padFiles = r.files;
    padNames = r.pads.map(p => p.name);
    if (!keepTarget) padTarget = 1;
    titleText = padTitle();
    userFiles = [PAD_ADD].concat(r.labels);
    openFileBrowser('user', 0);
    applyTitle();
    if (!r.files.length) toast('Use + ADD FILES, or put .wav .mp3 .pad .mid files in: ' + r.path, 8000);
  }

  const padInput = document.createElement('input');
  padInput.type = 'file'; padInput.multiple = true; padInput.accept = '.wav,.mp3,.pad,.mid,.midi';
  padInput.style.display = 'none';
  document.body.appendChild(padInput);
  padInput.addEventListener('change', async () => {
    const files = Array.from(padInput.files);
    let ok = 0;
    for (const f of files) {
      toast('Sending ' + f.name + ' ...', 8000);
      try {
        const r = await (await fetch('/api/padupload?name=' + encodeURIComponent(f.name), { method: 'POST', body: f })).json();
        if (r && r.ok) ok++; else toast('Not added: ' + ((r && r.error) || f.name), 6000);
      } catch (e) { toast('Engine not reachable', 5000); }
    }
    padInput.value = '';
    if (ok) { toast(ok + ' file(s) added to the Multipad folder', 4000); openPadBrowser(true); }
  });

  document.addEventListener('click', async ev => {          // ENTER inside the MULTIPAD list
    if (!ev.target.closest('[data-id="NAV-ENTER"]') || !inPad()) return;
    const i = filePage * filesPerPage + fileRow * 2 + fileColumn;
    if (i === 0) { padInput.click(); return; }
    const name = padFiles[i - 1];
    if (!name) return;
    toast('Loading ' + name + ' onto pad ' + padTarget + ' ...', 4000);
    const r = await api('/api/padload', { name: name, slot: padTarget });
    if (!r || !r.ok) { toast('Could not load ' + name + ((r && r.error) ? ': ' + r.error : ''), 6000); return; }
    padNames = r.pads.map(p => p.name);
    toast('PAD ' + padTarget + (r.names.length > 1 ? '..' + (padTarget + r.names.length - 1) : '') + ' = ' + r.names.join(', '), 5000);
    if (r.names.length === 1) padTarget = padTarget % 4 + 1;    // next load goes to the next pad
    titleText = padTitle(); applyTitle();
  });

  document.addEventListener('click', ev => {                // P-1..P-4: choose the target while loading, else play
    const el = ev.target.closest('[data-id]');
    if (!el || !/^P-[1-4]$/.test(el.dataset.id)) return;
    const n = Number(el.dataset.id.slice(2));
    if (inPad()) { padTarget = n; titleText = padTitle(); applyTitle(); return; }
    if (inCreate()) return;                                  // CREATE page: the panel's own behaviour
    api('/api/cmd', { op: 'pad', arg: n });
  });

  // ---- DRUM ENGINE -------------------------------------------------------------------------------
  // MENU > DRUM ENGINE (last root entry): opens the full page /drums (drumkits, 128-note map, samples, compatibility).
  if (typeof menuPages !== 'undefined') {
    const dRoot2 = Object.getOwnPropertyDescriptor(menuPages, 'root');
    if (dRoot2 && dRoot2.get) Object.defineProperty(menuPages, 'root', {
      configurable: true, enumerable: true,
      get() { return dRoot2.get.call(menuPages).concat(['DRUM ENGINE']); }
    });
    const prevOpenDrum = window.openSelectedMenu;
    window.openSelectedMenu = function () {
      if (menuPage === 'root' && dRoot2 && dRoot2.get && menuIndex === dRoot2.get.call(menuPages).length) {
        window.open('/drums', '_blank');
        return;
      }
      return prevOpenDrum ? prevOpenDrum.apply(this, arguments) : undefined;
    };
  }

  // ---- tempo and master volume -------------------------------------------
  const bpmBox = document.getElementById('bpm');
  if (bpmBox) {
    // One request at a time, always carrying the LATEST value: quick presses can no longer arrive out of order.
    let tempoBusy = false, tempoSent = null;
    const pushTempo = async () => {
      if (tempoBusy) return;
      tempoBusy = true;
      try {
        while (tempoSent !== Number(bpmBox.value)) {
          tempoSent = Number(bpmBox.value);
          await api('/api/cmd', { op: 'tempo', arg: tempoSent });
        }
      } finally { tempoBusy = false; }
    };
    bpmBox.addEventListener('input', () => {
      if (fromServer || !st.loaded) return;
      pushTempo();
    });
  }
  // MASTER and VOL-1..6 are changed from the LCD (NAV2 -/+); the panel then redraws that screen
  // through showComponentStatus(), so we send the new value from there.
  const origStatus = window.showComponentStatus;
  window.showComponentStatus = function (component) {
    if (origStatus) origStatus.apply(this, arguments);
    if (component === 'MASTER' && typeof masterVolume !== 'undefined') {
      api('/api/cmd', { op: 'master', arg: masterVolume });
    } else if (/^VOL-\d$/.test(component) && typeof componentVolumes !== 'undefined') {
      api('/api/cmd', { op: 'partvol', arg: component, value: componentVolumes[component] });
    }
  };

  // ---- TAP tempo, MUTE, EFX, MENU, registrations ------------------------------------------------
  let taps = [];
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-id]');
    if (!el) return;
    const id = el.dataset.id;
    if (id === 'TEMPO-TAP') {
      const now = Date.now();
      taps = taps.filter(t => now - t < 2500);
      taps.push(now);
      if (taps.length >= 3 && bpmBox) {
        const avg = (taps[taps.length - 1] - taps[0]) / (taps.length - 1);
        bpmBox.value = Math.max(20, Math.min(300, Math.round(60000 / avg)));
        bpmBox.dispatchEvent(new Event('input', { bubbles: true }));
      }
    } else if (id === 'MUTE-TAP') api('/api/cmd', { op: 'mute' });          // silences the sound
    else if (id === 'EFX') api('/api/cmd', { op: 'efx' });                  // reverb + chorus on/off
    else if (id === 'PERC-MENU2' && typeof showRootMenu === 'function') showRootMenu();
  });

  // R-1..R-4: tap = recall, hold (or right-click) = save the current style, section, tempo and volumes
  let regTimer = null, regHeld = false;
  async function regSave(n) {
    const vols = typeof componentVolumes !== 'undefined' ? Object.assign({}, componentVolumes) : {};
    await api('/api/cmd', { op: 'reg_save', arg: n, vols: vols, master: typeof masterVolume !== 'undefined' ? masterVolume : 100 });
    toast('REGISTRATION ' + n + ' SAVED');
  }
  async function regRecall(n) {
    const r = await api('/api/cmd', { op: 'reg_recall', arg: n });
    if (!r || !r.ok) { toast('REGISTRATION ' + n + ' IS EMPTY - hold the button to save'); return; }
    if (typeof componentVolumes !== 'undefined' && r.vols) Object.assign(componentVolumes, r.vols);
    if (typeof masterVolume !== 'undefined') masterVolume = r.master;
    toast('REGISTRATION ' + n + ' RECALLED');
  }
  document.addEventListener('pointerdown', ev => {
    const b = ev.target.closest('[data-id^="R-"]');
    if (!b || !/^R-[1-4]$/.test(b.dataset.id)) return;
    clearTimeout(regTimer);
    regHeld = false;
    regTimer = setTimeout(() => { regHeld = true; regSave(Number(b.dataset.id.slice(2))); }, 700);
  }, true);
  ['pointerup', 'pointercancel'].forEach(n => document.addEventListener(n, () => clearTimeout(regTimer), true));
  document.addEventListener('click', ev => {
    const b = ev.target.closest('[data-id^="R-"]');
    if (!b || !/^R-[1-4]$/.test(b.dataset.id)) return;
    if (regHeld) { regHeld = false; return; }
    regRecall(Number(b.dataset.id.slice(2)));
  });
  document.addEventListener('contextmenu', ev => {
    const b = ev.target.closest('[data-id^="R-"]');
    if (b && /^R-[1-4]$/.test(b.dataset.id)) { ev.preventDefault(); regSave(Number(b.dataset.id.slice(2))); }
  });

  // ---- state from the engine ---------------------------------------------
  // Lamps use the panel's own look: `active` = lit bulb (blue glow). The section playing right now also
  // pulses with the tempo (the panel's own beat-pulse animation); sections the style lacks are dimmed.
  function paintLamps() {
    const mu = $id('MUTE-TAP'), fx = $id('EFX');
    if (mu) mu.classList.toggle('active', !!st.muted);
    if (fx) fx.classList.toggle('active', !!st.efx);
    if (!inCreate()) (st.pads || []).forEach((p, i) => {      // pad lamps: lit while that pad plays
      const el = $id('P-' + (i + 1));
      if (el) el.classList.toggle('active', !!p.playing);
    });
    if (st.pad_error && st.pad_error !== lastPadError) toast(st.pad_error, 5000);
    lastPadError = st.pad_error || '';
    if (!st.loaded || inCreate()) return;
    const sec = st.section || '';
    const last = sec.slice(-1);
    const lit = {}, playing = {};
    if (st.running) {
      lit['SY-1'] = true;
      if (sec.indexOf('Intro ') === 0) playing['I-' + last] = true;
      else if (sec === 'Fill In BA') playing['BR-1'] = true;
      else if (sec.indexOf('Ending ') === 0) playing['EN-' + (sec.charCodeAt(sec.length - 1) - 64)] = true;
      else if (sec) playing['V-' + last] = true;          // Main X, or the fill leading into it
    } else if (st.intro) lit['I-' + st.intro] = true;
    if (st.main) lit['V-' + st.main.slice(-1)] = true;
    if (st.auto_fill) lit['AFH'] = true;
    LAMPS.forEach(id => {
      const el = $id(id);
      if (!el) return;
      const need = NEEDS_SECTION[id];
      const has = !need || !st.available || st.available.indexOf(need) >= 0;
      el.classList.toggle('active', has && (!!lit[id] || !!playing[id]));
      el.style.opacity = has ? '' : '0.4';
      const bulb = el.querySelector('.bulb');
      if (bulb) bulb.style.animation = playing[id] ? 'beat-pulse var(--beat-duration, 0.5s) linear infinite' : '';
    });
  }

  async function poll() {
    const s = await api('/api/state');
    if (s) {
      st = s;
      if (st.rev !== lastRev) {                  // a new style was loaded
        if (lastRev !== -1 && bpmBox && st.loaded) {
          fromServer = true;
          bpmBox.value = Math.round(st.bpm);
          bpmBox.dispatchEvent(new Event('input', { bubbles: true }));
          fromServer = false;
          toast(st.message || ('Loaded ' + st.name), 4000);
        }
        lastRev = st.rev;
      }
      paintLamps();
    } else {
      st = { loaded: false };
    }
    renderBadge();
  }
  setInterval(poll, 250);
  poll();
})();
