"""DRUM ENGINE: compact MIDI destination and kit setup page served at /drums."""
PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DX Arranger - DRUM ENGINE</title>
<style>
:root{color-scheme:dark;--bg:#080d16;--panel:#111b2b;--line:#263955;--text:#e2efff;--dim:#a2b3c9;--blue:#47b8ff;--green:#63dfa0;--red:#ff9292}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:760px;margin:24px auto;padding:0 16px}section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin:14px 0}
h1{font-size:22px;margin:0}h2{font-size:16px;color:var(--blue);margin:0 0 10px}.hint{color:var(--dim);font-size:14px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}button,select,input{font:inherit;color:var(--text);background:#0b1422;border:1px solid var(--line);border-radius:7px;padding:9px 11px}button{cursor:pointer}button:hover{border-color:var(--blue)}input[type=text]{flex:1;min-width:220px}select{min-width:220px}
#msg{min-height:1.5em;color:var(--green)}.bad{color:var(--red)}.ok{color:var(--green)}#kitInfo{color:var(--dim)}
</style></head><body><main>
<h1>DRUM ENGINE</h1>
<p class="hint">Choose where drum MIDI goes. Your arranger and beat workflow stay the same. Output changes take effect after restarting DX-Arranger.</p>
<section><h2>1. MIDI drum output</h2><div class="row"><label for="output">Send drum MIDI to</label><select id="output"><option value="internal">Internal drum kits</option><option value="midiout">MIDI port</option></select><button id="saveOutput">Save choice</button></div><div class="hint">MIDI port sends the drum MIDI out of the port named in config.json (\"midi_out_name\", for example a loopMIDI port).</div></section>
<section><h2>2. Internal drumkit</h2><div class="row"><select id="kits" aria-label="Available drumkits"></select><button id="selectKit">Use selected kit</button></div><div class="row"><input id="kitPath" type="text" placeholder="Path to kit folder (WAV/NCW/SF2 samples)"><button id="addKit">Load kit folder</button></div><div id="kitInfo" class="hint">Loading kit list…</div></section>
<section><h2>3. Extra samples (optional)</h2><div class="row"><input id="samplePath" type="text" placeholder="Path to additional WAV/NCW/SF2 sample folder"><button id="addSamples">Add sample folder</button></div><p class="hint">A sample folder adds files to the browser. MIDI notes only play samples assigned in the selected kit map. Recognizable names such as <code>36_kick.wav</code> are mapped automatically on a kit's first load.</p></section>
<section><h2>4. Play</h2><p>Save the output choice and restart DX-Arranger. Then use the usual arranger panel to load your beat/style and press Play.</p><p class="hint">WAV, NCW and limited SF2 sample-pool reading are supported. NKI/NKM instruments and other formats are listed but not played.</p></section>
<div id="msg" role="status" aria-live="polite"></div>
</main><script>
const $=id=>document.getElementById(id);
async function post(data){try{const r=await fetch('/api/drums',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return await r.json()}catch(e){return{error:'Could not contact DX-Arranger.'}}}
function say(text,bad=false){$('msg').textContent=text||'';$('msg').className=bad?'bad':'ok'}
function show(s){$('output').value=s.midi_output||'internal';const list=$('kits');list.replaceChildren();for(const k of (s.kits||[])){const o=document.createElement('option');o.value=k.id;o.textContent=k.name+(k.builtin?'':' (custom)');o.selected=k.id===s.selected;list.appendChild(o)}if(!list.options.length){const o=document.createElement('option');o.value='';o.textContent='No kits registered';list.appendChild(o)}$('kitInfo').textContent=s.kit?`Active kit: ${s.kit.name}; ${s.kit.assigned} notes mapped; ${s.kit.folder}`:'No kit selected yet. Load a folder containing a kit.'}
async function refresh(){const s=await post({op:'state'});if(s.error)say(s.error,true);else show(s)}
$('saveOutput').onclick=async()=>{const r=await post({op:'set_midi_output',target:$('output').value});say(r.error||r.message,!!r.error)};
$('selectKit').onclick=async()=>{if(!$('kits').value)return say('Choose or load a kit folder first.',true);const r=await post({op:'select_kit',id:$('kits').value});if(r.error)say(r.error,true);else{say('Drumkit loaded.');refresh()}};
$('addKit').onclick=async()=>{const path=$('kitPath').value.trim();if(!path)return say('Enter the kit folder path.',true);const r=await post({op:'add_kit',path});if(r.error)return say(r.error,true);if(r.state)show(r.state);say('Drumkit loaded. Check that the mapped notes match your beat.');$('kitPath').value=''};
$('addSamples').onclick=async()=>{const path=$('samplePath').value.trim();if(!path)return say('Enter the sample folder path.',true);const r=await post({op:'add_sample_folder',path});if(r.error)return say(r.error,true);if(r.state)show(r.state);say('Sample folder added. It does not automatically change the kit note map.');$('samplePath').value=''};
refresh();
</script></body></html>'''
