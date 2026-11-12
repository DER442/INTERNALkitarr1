#!/usr/bin/env python3
"""NCW -> WAV command-line converter (best effort; uses the same decoder DX-Arranger plays NCW files with).

Usage:  python ncw2wav.py input.ncw [output.wav]
Format details and limits: see drum_engine/ncw.py.
"""
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drum_engine import ncw  # noqa: E402


def to_wav(src, dst):
    hdr, a = ncw.decode(Path(src).read_bytes())
    bits = hdr["bits"]
    ints = np.clip(np.round(a.astype(np.float64) * (1 << (bits - 1))), -(1 << (bits - 1)), (1 << (bits - 1)) - 1).astype(np.int64)
    if bits == 16:
        raw = ints.astype("<i2").tobytes()
    elif bits == 24:
        raw = ints.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    else:
        raw = ints.astype("<i4").tobytes()
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(hdr["channels"])
        wf.setsampwidth(bits // 8)
        wf.setframerate(hdr["rate"])
        wf.writeframes(raw)
    return hdr


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".wav")
    try:
        hdr = to_wav(src, dst)
    except (ncw.NcwError, OSError) as err:
        print(f"Error: {err}")
        sys.exit(1)
    print(f"Wrote {dst}: channels={hdr['channels']}, bits={hdr['bits']}, rate={hdr['rate']}, frames={hdr['frames']}")


if __name__ == "__main__":
    main()
