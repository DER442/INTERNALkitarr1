import os, struct, sys, math
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def make_wav(path, data, rate=44100, bits=16, tag=1, extensible=False, extra_chunk=False, bad_size=False, header_only=False):
    """data: float array (n,) or (n, ch) in -1..1. Writes a WAV by hand (so the reader is tested against known bytes)."""
    a = np.asarray(data, np.float64)
    if a.ndim == 1:
        a = a[:, None]
    ch = a.shape[1]
    if tag == 3:
        raw = a.astype("<f4").tobytes() if bits == 32 else a.astype("<f8").tobytes()
    elif bits == 8:
        raw = (np.round(a * 127) + 128).astype(np.uint8).tobytes()
    elif bits == 16:
        raw = np.round(a * 32767).astype("<i2").tobytes()
    elif bits == 24:
        v = np.round(a * 8388607).astype(np.int32).reshape(-1)
        raw = b"".join(struct.pack("<i", int(x))[:3] for x in v)
    else:
        raw = np.round(a * 2147483647).astype("<i4").tobytes()
    if header_only:
        raw = b""
    fmt_tag = 0xFFFE if extensible else tag
    fmt = struct.pack("<HHIIHH", fmt_tag, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
    if extensible:
        fmt += struct.pack("<HHI", 22, bits, 0) + struct.pack("<H", tag) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if extra_chunk:
        body += b"LIST" + struct.pack("<I", 5) + b"abcde" + b"\x00"       # odd size -> pad byte
        body += b"smpl" + struct.pack("<I", 36) + struct.pack("<IIIIIIIII", 0, 0, 0, 60, 0, 0, 0, 0, 0)
    size = len(raw) + (1 if len(raw) & 1 else 0)
    body += b"data" + struct.pack("<I", 0xFFFFFFFF if bad_size else len(raw)) + raw + (b"\x00" if len(raw) & 1 else b"")
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)


def tone(n=2000, freq=440.0, rate=44100, amp=0.5):
    return amp * np.sin(2 * math.pi * freq * np.arange(n) / rate)


def click(n=4000, amp=0.5):
    """A constant-level 'hit': easy to measure exactly (every sample = amp)."""
    return np.full(n, amp)


def make_sf2(path, samples, major=2):
    """Minimal SoundFont: samples = [(name, int16 array, rate)]."""
    smpl = b"".join(np.asarray(s, "<i2").tobytes() + b"\x00" * 92 for _, s, _ in samples)   # 46 zero frames padding each
    shdr, pos = b"", 0
    for name, s, rate in samples:
        n = len(s)
        shdr += name.encode().ljust(20, b"\0") + struct.pack("<IIIIIBbHH", pos, pos + n, pos, pos + n, rate, 60, 0, 0, 1)
        pos += n + 46
    shdr += b"EOS".ljust(20, b"\0") + struct.pack("<IIIIIBbHH", 0, 0, 0, 0, 0, 0, 0, 0, 0)
    def ck(i, d): return i + struct.pack("<I", len(d)) + d + (b"\0" if len(d) & 1 else b"")
    info = ck(b"ifil", struct.pack("<HH", major, 1))
    body = b"sfbk" + ck(b"LIST", b"INFO" + info) + ck(b"LIST", b"sdta" + ck(b"smpl", smpl)) + ck(b"LIST", b"pdta" + ck(b"shdr", shdr))
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)


# ---------------------------------------------------------------------------------------------------------------------------
def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def make_sty(path, sections, ppq=480, bpm=120, tempo_changes=(), setup=True):
    """Write a Yamaha-style MIDI file.
    sections: [(name, bars, [(tick_in_section, channel_1based, note, vel, length_ticks)])]  -- sections follow each other.
    tempo_changes: [(absolute_tick, bpm)].  Markers: SFF1 (setup, with a program change + CC7) then one per section."""
    ev = [(0, 0, b"\xff\x58\x04\x04\x02\x18\x08"), (0, 0, b"\xff\x51\x03" + int(60_000_000 / bpm).to_bytes(3, "big"))]
    for t, b in tempo_changes:
        ev.append((t, 0, b"\xff\x51\x03" + int(60_000_000 / b).to_bytes(3, "big")))
    pos = 0
    if setup:
        ev.append((0, 1, b"\xff\x06\x04SFF1"))
        ev.append((0, 2, b"\xc9\x00")); ev.append((0, 2, bytes([0xB9, 7, 100])))
        pos = ppq * 4                                   # one bar of setup
    for name, bars, notes in sections:
        nb = name.encode()
        ev.append((pos, 1, b"\xff\x06" + _vlq(len(nb)) + nb))
        for t, ch, note, vel, ln in notes:
            ev.append((pos + t, 3, bytes([0x90 | (ch - 1), note, vel])))
            ev.append((pos + t + ln, 0, bytes([0x80 | (ch - 1), note, 0])))
        pos += bars * ppq * 4
    ev.append((pos, 9, b"\xff\x2f\x00"))
    ev.sort(key=lambda e: (e[0], e[1]))
    body, last = b"", 0
    for t, _, data in ev:
        body += _vlq(t - last) + data
        last = t
    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq) + b"MTrk" + struct.pack(">I", len(body)) + body)
    return path
