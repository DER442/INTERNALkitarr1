"""sty_parse.py - READ-ONLY track parser for .STY files (Stage 2).

Usage:  python sty_parse.py yourfile.STY

Reads the first MTrk chunk (standard MIDI layout) and prints:
  - header info, time signature, tempo, track name
  - every marker (FF 06) with its position
  - for each marker segment: note counts per MIDI channel
Nothing is written to the file. Nothing is guessed: what the bytes
say is printed; what we do not understand is counted, not hidden.
"""
import struct
import sys
from collections import defaultdict


def read_vlq(buf, pos):
    """Variable-length quantity: 7 bits per byte, top bit = 'more follows'."""
    value = 0
    for _ in range(4):
        if pos >= len(buf):
            raise ValueError("ran out of bytes inside a variable-length number")
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            return value, pos
    raise ValueError("variable-length number longer than 4 bytes")


def text_of(raw):
    return raw.decode("latin-1", errors="replace")


def parse_track(trk):
    """Return a list of events: (tick, kind, info). Stops safely on errors."""
    events = []
    pos, tick, running = 0, 0, None
    try:
        while pos < len(trk):
            delta, pos = read_vlq(trk, pos)
            tick += delta
            status = trk[pos]
            if status == 0xFF:  # meta event
                mtype = trk[pos + 1]
                length, pos = read_vlq(trk, pos + 2)
                data = trk[pos:pos + length]
                if len(data) < length:
                    raise ValueError("meta event runs past end of track")
                pos += length
                events.append((tick, "meta", (mtype, data)))
                running = None
                if mtype == 0x2F:
                    break
            elif status in (0xF0, 0xF7):  # sysex
                length, pos = read_vlq(trk, pos + 1)
                data = trk[pos:pos + length]
                if len(data) < length:
                    raise ValueError("sysex runs past end of track")
                pos += length
                events.append((tick, "sysex", data))
                running = None
            else:
                if status & 0x80:
                    running = status
                    pos += 1
                elif running is None:
                    raise ValueError(f"data byte 0x{status:02X} with no running status")
                kind = running & 0xF0
                chan = (running & 0x0F) + 1
                n = 1 if kind in (0xC0, 0xD0) else 2
                data = trk[pos:pos + n]
                if len(data) < n:
                    raise ValueError("channel message runs past end of track")
                pos += n
                events.append((tick, "chan", (kind, chan, data)))
    except (ValueError, IndexError) as err:
        print(f"\n! PARSE STOPPED at track byte {pos}: {err}")
        print("  (events read so far are still shown)")
    return events


def main():
    if len(sys.argv) != 2:
        print("Usage: python sty_parse.py yourfile.STY")
        return
    with open(sys.argv[1], "rb") as f:  # read-only
        data = f.read()

    if data[:4] != b"MThd":
        print("Not a MIDI-based file (no MThd). Stopping.")
        return
    hlen = struct.unpack(">I", data[4:8])[0]
    fmt, ntrks, ppq = struct.unpack(">HHH", data[8:14])
    pos = 8 + hlen
    if data[pos:pos + 4] != b"MTrk":
        print("No MTrk right after header. Stopping.")
        return
    size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
    trk = data[pos + 8:pos + 8 + size]
    print(f"Header: format {fmt}, {ntrks} track(s), {ppq} ticks/quarter")
    print(f"Track 0: {len(trk)} bytes of events")

    events = parse_track(trk)
    print(f"Events parsed: {len(events)}")

    num, den = 4, 4
    for tick, kind, info in events:
        if kind == "meta" and info[0] == 0x58 and len(info[1]) >= 2:
            num, den = info[1][0], 2 ** info[1][1]
            print(f"Time signature: {num}/{den} (at tick {tick})")
            break
    bar_ticks = ppq * 4 * num // den

    def where(t):
        return f"bar {t // bar_ticks + 1}, beat {(t % bar_ticks) // ppq + 1}"

    for tick, kind, info in events:
        if kind == "meta" and info[0] == 0x51 and len(info[1]) == 3:
            us = int.from_bytes(info[1], "big")
            print(f"Tempo: {us} microseconds/quarter = {60_000_000 / us:.2f} BPM (tick {tick})")
            break
    for tick, kind, info in events:
        if kind == "meta" and info[0] == 0x03:
            print(f"Track name: '{text_of(info[1])}'")
            break

    # Split into segments at each marker (FF 06)
    segments = [("(start, before first marker)", 0, defaultdict(int), [0])]
    for tick, kind, info in events:
        if kind == "meta" and info[0] == 0x06:
            segments.append((text_of(info[1]), tick, defaultdict(int), [0]))
        elif kind == "chan" and info[0] == 0x90 and len(info[2]) == 2 and info[2][1] > 0:
            segments[-1][2][info[1]] += 1
        elif kind == "sysex":
            segments[-1][3][0] += 1

    print(f"\n== Markers found: {len(segments) - 1} ==")
    for name, tick, notes, sx in segments:
        total = sum(notes.values())
        chans = ", ".join(f"ch{c}:{n}" for c, n in sorted(notes.items())) or "none"
        print(f"\n'{name}'  tick {tick} ({where(tick)})")
        print(f"   note-ons: {total}  [{chans}]   sysex: {sx[0]}")

    ends = [t for t, k, i in events if k == "meta" and i[0] == 0x2F]
    if ends:
        print(f"\nEnd-of-track at tick {ends[0]} ({where(ends[0])})")
    print("\nDone.")


if __name__ == "__main__":
    main()
