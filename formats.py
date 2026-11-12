"""Sample file formats: probe (cheap metadata + honest status), load (decoded float32 audio), scan (a folder).

Supported:  .wav  (PCM 8/16/24/32, float 32/64, WAVE_FORMAT_EXTENSIBLE; the 'smpl' chunk's root note is read)
            .sf2  (the raw sample pool only - presets/zones are not interpreted; members are 'file.sf2#<n>')
            .ncw  (decoded in-process by ncw.py)
Everything else the engine knows about is listed in KNOWN_UNSUPPORTED and reported as 'unsupported'.

Status values: ok | missing | unsupported | unreadable   (always with a human-readable reason when not ok)
"""
import os
import struct

import numpy as np

from . import ncw


class FormatError(Exception):
    """The file is damaged or not what its extension says."""


class UnsupportedFormat(Exception):
    """A real format this build cannot play."""


KNOWN_UNSUPPORTED = {
    ".nki": "NKI is a Kontakt instrument, not a sample file; export or convert its samples to WAV",
    ".nkm": "NKM is a Kontakt multi; export or convert its samples to WAV",
    ".nkc": "NKC is Kontakt cache data, not audio",
    ".nkx": "NKX is a Kontakt resource container; its samples cannot be read directly",
    ".sf3": "SF3 (compressed SoundFont) is not supported; convert it to SF2 or WAV",
    ".mp3": "MP3 is not supported; convert it to WAV",
    ".ogg": "OGG is not supported; convert it to WAV",
    ".flac": "FLAC is not supported; convert it to WAV",
    ".aif": "AIFF is not supported; convert it to WAV",
    ".aiff": "AIFF is not supported; convert it to WAV",
    ".m4a": "M4A is not supported; convert it to WAV",
    ".wv": "WavPack is not supported; convert it to WAV",
    ".exs": "EXS is a Logic sampler instrument, not a sample file",
    ".gig": "GIG (Gigastudio) is not supported",
}
_SUPPORTED = (".wav", ".sf2", ".ncw")


def supported_exts():
    return sorted(_SUPPORTED)


def split_ref(ref):
    """'dir/pool.sf2#3' -> ('dir/pool.sf2', 3);  'kick.wav' -> ('kick.wav', None)."""
    real, sep, frag = str(ref).rpartition("#")
    if sep and frag.isdigit():
        return real, int(frag)
    return str(ref), None


# ============================== data objects ==================================================================
class Sample:
    """Decoded audio: `data` is float32, shape (frames,) for mono or (frames, 2) for stereo, at `rate`."""
    __slots__ = ("data", "rate", "meta")

    def __init__(self, data, rate, meta=None):
        self.data, self.rate, self.meta = data, rate, meta or {}

    @property
    def frames(self):
        return self.data.shape[0]

    @property
    def channels(self):
        return 1 if self.data.ndim == 1 else self.data.shape[1]

    @property
    def nbytes(self):
        return self.data.nbytes


class Info:
    def __init__(self, ref, name, ext, status="ok", reason="", frames=0, rate=0, channels=0, bits=0, meta=None):
        self.ref, self.name, self.ext, self.status, self.reason = ref, name, ext, status, reason
        self.frames, self.rate, self.channels, self.bits, self.meta = frames, rate, channels, bits, meta or {}

    def to_json(self):
        return dict(ref=self.ref, name=self.name, ext=self.ext, status=self.status, reason=self.reason,
                    frames=self.frames, rate=self.rate, channels=self.channels, bits=self.bits,
                    seconds=round(self.frames / self.rate, 3) if self.rate else 0.0, meta=dict(self.meta))


# ============================== WAV ============================================================================
def _riff_chunks(data, form):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != form:
        raise FormatError(f"not a RIFF/{form.decode()} file")
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def _wav_parse(data):
    """-> (fmt dict, data_start, data_len, root_note or None). Raises FormatError / UnsupportedFormat."""
    fmt, dstart, dlen, root = None, None, 0, None
    for cid, start, size in _riff_chunks(data, b"WAVE"):
        if cid == b"fmt ":
            if size < 16 or start + 16 > len(data):
                raise FormatError("WAV 'fmt ' chunk is too short")
            tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", data, start)
            if tag == 0xFFFE:
                if size < 26 or start + 26 > len(data):
                    raise FormatError("WAV extensible 'fmt ' chunk is too short")
                tag = struct.unpack_from("<H", data, start + 24)[0]
            fmt = dict(tag=tag, channels=ch, rate=rate, bits=bits)
        elif cid == b"smpl" and size >= 16 and start + 16 <= len(data):
            root = struct.unpack_from("<I", data, start + 12)[0]
        elif cid == b"data":
            dstart = start
            dlen = size if size <= len(data) - start else len(data) - start      # 0xFFFFFFFF / lying sizes
            break                                                                # nothing we need follows the audio
    if fmt is None:
        raise FormatError("WAV has no 'fmt ' chunk")
    if dstart is None or dlen <= 0:
        raise FormatError("WAV contains no audio data")
    tag, bits = fmt["tag"], fmt["bits"]
    if tag == 1 and bits in (8, 16, 24, 32):
        pass
    elif tag == 3 and bits in (32, 64):
        pass
    elif tag in (1, 3):
        raise UnsupportedFormat(f"WAV with {bits} bits per sample is not supported")
    else:
        names = {2: "ADPCM", 0x55: "MP3", 6: "A-law", 7: "mu-law", 0x11: "IMA ADPCM"}
        raise UnsupportedFormat(f"WAV compression {names.get(tag, '0x%04X' % tag)} is not supported")
    if fmt["channels"] < 1 or fmt["rate"] < 1000:
        raise FormatError(f"WAV header is invalid (channels={fmt['channels']}, rate={fmt['rate']})")
    return fmt, dstart, dlen, (root if root is not None and 0 <= root <= 127 else None)


def _wav_decode(data, fmt, start, length):
    ch, bits, tag = fmt["channels"], fmt["bits"], fmt["tag"]
    width = bits // 8
    frames = length // (width * ch)
    if frames <= 0:
        raise FormatError("WAV contains no complete audio frame")
    raw = data[start:start + frames * width * ch]
    if tag == 3:
        a = np.frombuffer(raw, "<f4" if bits == 32 else "<f8").astype(np.float32)
        a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
    elif bits == 8:
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif bits == 16:
        a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        a = ((v ^ 0x800000) - 0x800000).astype(np.float32) / 8388608.0
    else:
        a = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    return a.reshape(frames, ch)


# ============================== common post-processing =========================================================
def _finish(a, rate, engine_rate, meta):
    """(frames, ch) float32 -> Sample: first two channels, identical channels stored as mono, resampled to engine_rate."""
    a = a[:, :2] if a.shape[1] > 2 else a
    if a.shape[1] == 2 and np.array_equal(a[:, 0], a[:, 1]):
        a = a[:, :1]
    if a.shape[1] == 1:
        a = a[:, 0]
    if engine_rate and rate != engine_rate:
        n = a.shape[0]
        m = max(1, int(round(n * engine_rate / rate)))
        x, xp = np.linspace(0, n - 1, m), np.arange(n)
        a = np.interp(x, xp, a) if a.ndim == 1 else np.stack([np.interp(x, xp, a[:, c]) for c in range(a.shape[1])], 1)
        rate = engine_rate
    return Sample(np.ascontiguousarray(a, dtype=np.float32), rate, meta)


# ============================== SoundFont (sample pool only) =====================================================
def _sf2_scan(f, size):
    """-> (major, smpl_offset, smpl_bytes, [(name, start, end, rate, pitch, type), ...]). Reads only the metadata."""
    def head(pos):
        f.seek(pos)
        h = f.read(8)
        return (h[:4], struct.unpack("<I", h[4:])[0]) if len(h) == 8 else (b"", 0)
    f.seek(0)
    h = f.read(12)
    if len(h) < 12 or h[:4] != b"RIFF" or h[8:12] != b"sfbk":
        raise FormatError("not a SoundFont (RIFF/sfbk) file")
    major, smpl_off, smpl_len, shdr = 0, 0, 0, None
    pos = 12
    while pos + 8 <= size:
        cid, csz = head(pos)
        if cid == b"LIST":
            f.seek(pos + 8)
            kind = f.read(4)
            sub, end = pos + 12, pos + 8 + csz
            while sub + 8 <= min(end, size):
                sid, ssz = head(sub)
                if kind == b"INFO" and sid == b"ifil":
                    f.seek(sub + 8)
                    major = struct.unpack("<H", f.read(2))[0]
                elif kind == b"sdta" and sid == b"smpl":
                    smpl_off, smpl_len = sub + 8, ssz
                elif kind == b"pdta" and sid == b"shdr":
                    f.seek(sub + 8)
                    shdr = f.read(ssz)
                sub += 8 + ssz + (ssz & 1)
        pos += 8 + csz + (csz & 1)
    if major >= 3:
        raise UnsupportedFormat("SF3 (compressed SoundFont) is not supported; convert it to SF2 or WAV")
    if shdr is None or smpl_len <= 0:
        raise FormatError("SoundFont has no sample data")
    out = []
    for i in range(len(shdr) // 46 - 1):                       # the last record is the EOS terminator
        r = shdr[i * 46:(i + 1) * 46]
        name = r[:20].split(b"\0")[0].decode("latin-1").strip()
        start, end, _, _, rate, pitch, _, _, typ = struct.unpack("<IIIIIBbHH", r[20:])
        out.append((name or f"sample {i}", start, end, rate, pitch, typ))
    return major, smpl_off, smpl_len, out


def _sf2_member_ok(m, smpl_len):
    name, start, end, rate, pitch, typ = m
    if typ & 0x8000:
        return "unsupported", "ROM sample (data is not stored in the file)"
    if end <= start or end * 2 > smpl_len:
        return "unreadable", "sample bounds lie outside the SoundFont's sample data"
    if rate < 1000:
        return "unreadable", f"implausible sample rate {rate}"
    return "ok", ""


def _sf2_infos(path):
    base = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            major, off, slen, members = _sf2_scan(f, os.path.getsize(path))
    except UnsupportedFormat as err:
        return [Info(path, base, ".sf2", "unsupported", str(err))]
    except (FormatError, struct.error, OSError) as err:
        return [Info(path, base, ".sf2", "unreadable", str(err) or "damaged SoundFont")]
    infos = []
    for i, m in enumerate(members):
        status, why = _sf2_member_ok(m, slen)
        meta = {"root_note": m[4]} if 0 <= m[4] <= 127 else {}
        infos.append(Info(f"{path}#{i}", m[0], ".sf2", status, why, frames=max(0, m[2] - m[1]), rate=m[3],
                          channels=1, bits=16, meta=meta))
    if not infos:
        infos.append(Info(path, base, ".sf2", "unreadable", "SoundFont contains no samples"))
    return infos


def _sf2_load(path, idx, engine_rate):
    if idx is None:
        raise FormatError("a SoundFont file needs a member number ('file.sf2#0')")
    with open(path, "rb") as f:
        _, off, slen, members = _sf2_scan(f, os.path.getsize(path))
        if idx >= len(members):
            raise FormatError(f"SoundFont has no sample #{idx}")
        status, why = _sf2_member_ok(members[idx], slen)
        if status == "unsupported":
            raise UnsupportedFormat(why)
        if status != "ok":
            raise FormatError(why)
        name, start, end, rate, pitch, _ = members[idx]
        f.seek(off + start * 2)
        raw = f.read((end - start) * 2)
    if len(raw) < (end - start) * 2:
        raise FormatError("SoundFont sample data is truncated")
    a = (np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0).reshape(-1, 1)
    return _finish(a, rate, engine_rate, {"root_note": pitch} if 0 <= pitch <= 127 else {})


# ============================== NCW =============================================================================
def _ncw_probe(path, ref, name):
    with open(path, "rb") as f:
        data = f.read()
    try:
        hdr = ncw.probe(data)
    except ncw.NcwError as err:
        return Info(ref, name, ".ncw", "unreadable", f"NCW: {err}")
    return Info(ref, name, ".ncw", "ok", frames=hdr["frames"], rate=hdr["rate"], channels=hdr["channels"],
                bits=hdr["bits"])


def _ncw_load(path, engine_rate):
    with open(path, "rb") as f:
        data = f.read()
    try:
        hdr, a = ncw.decode(data)
    except ncw.NcwError as err:
        raise FormatError(f"NCW: {err}")
    return _finish(a, hdr["rate"], engine_rate, {})


# ============================== public API ======================================================================
def probe(path, ref=None, name=None):
    """Metadata and honest status for one file ('x.sf2#n' members included), without decoding the audio."""
    ref = ref if ref is not None else path
    real, frag = split_ref(path)
    ext = os.path.splitext(real)[1].lower()
    name = name or os.path.basename(real)
    if not os.path.isfile(real):
        return Info(ref, name, ext, "missing", "file not found")
    if ext in KNOWN_UNSUPPORTED:
        return Info(ref, name, ext, "unsupported", KNOWN_UNSUPPORTED[ext])
    try:
        if ext == ".wav":
            with open(real, "rb") as f:
                data = f.read()
            fmt, ds, dl, root = _wav_parse(data)
            frames = dl // (fmt["bits"] // 8 * fmt["channels"])
            if frames <= 0:
                raise FormatError("WAV contains no complete audio frame")
            return Info(ref, name, ext, "ok", frames=frames, rate=fmt["rate"], channels=fmt["channels"],
                        bits=fmt["bits"], meta={"root_note": root} if root is not None else {})
        if ext == ".sf2":
            infos = _sf2_infos(real)
            if frag is None:
                return infos[0] if len(infos) == 1 else Info(ref, name, ext, "ok", frames=sum(i.frames for i in infos),
                                                            meta={"members": len(infos)})
            for i in infos:
                if i.ref.endswith("#%d" % frag):
                    i.ref = ref
                    return i
            return Info(ref, name, ext, "missing", f"SoundFont has no sample #{frag}")
        if ext == ".ncw":
            return _ncw_probe(real, ref, name)
    except UnsupportedFormat as err:
        return Info(ref, name, ext, "unsupported", str(err))
    except (FormatError, struct.error, ValueError) as err:
        return Info(ref, name, ext, "unreadable", str(err) or "damaged file")
    except OSError as err:
        return Info(ref, name, ext, "unreadable", str(err))
    return Info(ref, name, ext, "unsupported", f"unknown file type '{ext}'")


def load(path, engine_rate=44100):
    """Decode a sample (or 'x.sf2#n' member) to a Sample at engine_rate. Raises FormatError / UnsupportedFormat /
    FileNotFoundError."""
    real, frag = split_ref(path)
    ext = os.path.splitext(real)[1].lower()
    if ext in KNOWN_UNSUPPORTED:
        raise UnsupportedFormat(KNOWN_UNSUPPORTED[ext])
    if ext not in _SUPPORTED:
        raise UnsupportedFormat(f"unknown file type '{ext}'")
    if not os.path.isfile(real):
        raise FileNotFoundError(real)
    if ext == ".sf2":
        try:
            return _sf2_load(real, frag, engine_rate)
        except struct.error as err:
            raise FormatError("damaged SoundFont: " + str(err))
    if ext == ".ncw":
        if frag is not None:
            raise FormatError("an NCW file has no selectable members")
        return _ncw_load(real, engine_rate)
    with open(real, "rb") as f:
        data = f.read()
    try:
        fmt, ds, dl, root = _wav_parse(data)
        return _finish(_wav_decode(data, fmt, ds, dl), fmt["rate"], engine_rate,
                       {"root_note": root} if root is not None else {})
    except struct.error as err:
        raise FormatError("damaged WAV: " + str(err))


def scan(folder, limit=5000):
    """Every audio-looking file under `folder` (sub-folders included), good or bad - nothing is hidden. SoundFonts
    contribute one entry per sample."""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if fn.startswith(".") or (ext not in _SUPPORTED and ext not in KNOWN_UNSUPPORTED):
                continue
            p = os.path.join(root, fn)
            out.extend(_sf2_infos(p) if ext == ".sf2" else [probe(p)])
            if len(out) >= limit:
                return out
    return out
