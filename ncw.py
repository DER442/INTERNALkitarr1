"""NCW decoder (in-process, NumPy).

Port of the logic in ncw2wav.py, vectorised so a sample loads in milliseconds instead of seconds, and
with no subprocess (so it also works inside a packaged .exe / app where sys.executable is not Python).

Layout this decoder assumes (taken from ncw2wav.py - NOT verified against Native Instruments' own files):
  header   8-byte magic 01 A8 9E D6 3x 01 00 00 (x = 0 or 1), then little-endian
           u16 channels, u16 bits, u32 sample_rate, u32 num_samples @8..19,
           u32 blocks_offset, u32 data_offset, u32 data_size @0x14
  table    (num_blocks + 1) u32 offsets at blocks_offset, relative to data_offset; num_blocks = ceil(frames / 512)
  block    one 16-byte sub-block header per channel, each followed by its payload:
           u32 signature, i32 base, i16 bits, u16 flags, u32 unused
           bits > 0  delta coded: sample 0 = base, then running sum of the first 511 of 512 packed deltas
           bits < 0  raw samples, |bits| wide
           bits = 0  no payload: all 512 samples = base (my reading; the script rejected it)
           payload = 512 values, LSB-first bit-packed, signed
  stereo   flags bit 0 of the FIRST channel's sub-block = mid/side: L = M + S, R = M - S
Anything that does not fit is reported as NcwError instead of being guessed at.
"""
import struct

import numpy as np

SAMPLES_PER_BLOCK = 512
FILE_MAGIC = b"\x01\xa8\x9e\xd6"
FILE_VERSIONS = (0x30, 0x31)
# ncw2wav.py compares the little-endian read of the signature with 0x3E9A0C16; the byte order is easy to get
# backwards, so both orders are accepted.
SUBBLOCK_SIGS = (0x3E9A0C16, 0x160C9A3E)
HEADER_MIN = 120
SUBHEADER = struct.Struct("<IihHI")                  # 16 bytes
MAX_VALUES = 200_000_000                             # frames * channels; ~800 MB as float32


class NcwError(ValueError):
    """The file is not a readable NCW (the message says why)."""


def parse_header(data):
    if len(data) < HEADER_MIN:
        raise NcwError("NCW header truncated (file is smaller than 120 bytes)")
    if data[:4] != FILE_MAGIC:
        raise NcwError("not an NCW file (bad signature)")
    if data[4] not in FILE_VERSIONS or data[5:8] != b"\x01\x00\x00":
        raise NcwError("unrecognised NCW version bytes " + data[4:8].hex())
    channels, bits, rate, frames = struct.unpack_from("<HHII", data, 8)
    blocks_offset, data_offset, data_size = struct.unpack_from("<III", data, 0x14)
    if not 1 <= channels <= 8:
        raise NcwError(f"unsupported NCW channel count {channels}")
    if bits not in (16, 24, 32):
        raise NcwError(f"unsupported NCW bit depth {bits}")
    if not 1000 <= rate <= 768000:
        raise NcwError(f"implausible NCW sample rate {rate}")
    if frames <= 0:
        raise NcwError("NCW file contains no samples")
    if frames * channels > MAX_VALUES:
        raise NcwError("NCW file is too large to load into RAM")
    return dict(channels=channels, bits=bits, rate=rate, frames=frames, blocks_offset=blocks_offset,
                data_offset=data_offset, data_size=data_size)


def _block_table(data, hdr):
    nb = (hdr["frames"] + SAMPLES_PER_BLOCK - 1) // SAMPLES_PER_BLOCK
    end = hdr["blocks_offset"] + 4 * (nb + 1)
    if hdr["blocks_offset"] < 0 or end > len(data):
        raise NcwError("NCW block table lies outside the file")
    table = np.frombuffer(data, "<u4", nb + 1, hdr["blocks_offset"]).astype(np.int64)
    if np.any(np.diff(table) < 0):
        raise NcwError("NCW block table is not in ascending order")
    if hdr["data_offset"] + int(table[-1]) > len(data):
        raise NcwError("NCW file is truncated (block data runs past the end of the file)")
    return nb, table


def _unpack(payload, width, count):
    """`count` LSB-first, signed, `width`-bit values (width 1..32) as int64."""
    if count <= 0:
        return np.zeros(0, np.int64)
    nbits = count * width
    raw = np.frombuffer(payload, np.uint8, (nbits + 7) // 8)
    bits = np.unpackbits(raw, bitorder="little")[:nbits].reshape(count, width).astype(np.int64)
    vals = bits @ (np.int64(1) << np.arange(width, dtype=np.int64))
    sign = np.int64(1) << (width - 1)
    return (vals ^ sign) - sign


def _decode_subblock(payload, base, bits, need):
    """512 int64 samples. `need` = how many leading samples must really be present (the last block may be cut short)."""
    n = SAMPLES_PER_BLOCK
    if bits == 0:
        return np.full(n, base, np.int64)
    width = abs(bits)
    if width > 32:
        raise NcwError(f"unsupported NCW sub-block width {width}")
    have = min(n, len(payload) * 8 // width)
    out = np.zeros(n, np.int64)
    if bits > 0:
        if have < max(need - 1, 0):
            raise NcwError("NCW sub-block payload is truncated")
        deltas = _unpack(payload, width, have)
        steps = np.zeros(n, np.int64)
        k = min(have, n - 1)                          # the 512th delta is stored but never used
        steps[1:1 + k] = deltas[:k]
        out = base + np.cumsum(steps)
    else:
        if have < need:
            raise NcwError("NCW sub-block payload is truncated")
        out[:have] = _unpack(payload, width, have)
    return out


def _subheader(block, pos, where):
    if pos + SUBHEADER.size > len(block):
        raise NcwError(f"truncated NCW sub-block header at {where}")
    sig, base, bits, flags, _ = SUBHEADER.unpack_from(block, pos)
    if sig not in SUBBLOCK_SIGS:
        raise NcwError(f"bad NCW sub-block signature 0x{sig:08X} at {where}")
    return base, bits, flags


def probe(data):
    """Cheap structural check: header, block table and the first sub-block header. Returns the header dict."""
    hdr = parse_header(data)
    nb, table = _block_table(data, hdr)
    first = data[hdr["data_offset"] + int(table[0]):hdr["data_offset"] + int(table[1])]
    _subheader(first, 0, "block 0 ch 0")
    return hdr


def decode(data):
    """bytes -> (hdr, float32 array (frames, channels) in -1..1)."""
    hdr = parse_header(data)
    ch, frames = hdr["channels"], hdr["frames"]
    nb, table = _block_table(data, hdr)
    out = np.zeros((frames, ch), np.int64)
    for b in range(nb):
        start = hdr["data_offset"] + int(table[b])
        block = data[start:hdr["data_offset"] + int(table[b + 1])]
        in_block = min(SAMPLES_PER_BLOCK, frames - b * SAMPLES_PER_BLOCK)
        pos, chans, first_flags = 0, [], 0
        for c in range(ch):
            base, bits, flags = _subheader(block, pos, f"block {b} ch {c}")
            pos += SUBHEADER.size
            if c == 0:
                first_flags = flags
            size = (abs(bits) * SAMPLES_PER_BLOCK + 7) // 8
            chans.append(_decode_subblock(block[pos:pos + size], base, bits, in_block))
            pos += size
        if ch == 2 and first_flags & 1:
            mid, side = chans
            chans = [mid + side, mid - side]
        for c in range(ch):
            out[b * SAMPLES_PER_BLOCK:b * SAMPLES_PER_BLOCK + in_block, c] = chans[c][:in_block]
    scale = 1.0 / float(1 << (hdr["bits"] - 1))
    return hdr, np.clip(out * scale, -1.0, 1.0).astype(np.float32)
