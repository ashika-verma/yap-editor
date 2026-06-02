#!/usr/bin/env python3
"""
Audio enhancement pipeline: ffmpeg EQ + compression + loudness normalization.
Usage: python enhance.py <video_or_wav_path> <output_wav_path>
Outputs an enhanced WAV at 48kHz ready for video export.
"""
import sys
import os
import subprocess


def main():
    if len(sys.argv) < 3:
        print("Usage: enhance.py <input> <output_wav>", file=sys.stderr)
        sys.exit(1)

    src = sys.argv[1]
    dst = sys.argv[2]

    print("[enhance] applying EQ + de-ess + compression + loudnorm...", file=sys.stderr)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", src,
            "-af",
            # High-pass to cut room rumble below 80 Hz
            "highpass=f=80,"
            # Cut muddiness at 300 Hz
            "equalizer=f=300:width_type=o:width=2:g=-2,"
            # Subtle de-ess: tame harshness at 7 kHz
            "equalizer=f=7000:width_type=o:width=1:g=-1.5,"
            # Boost presence/clarity at 3 kHz
            "equalizer=f=3000:width_type=o:width=2:g=1.5,"
            # Soft 3:1 compression — evens out loud/quiet moments
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=50:makeup=2,"
            # Normalize to podcast standard: -16 LUFS, -1.5 dBTP true peak
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "48000",
            "-ac", "1",
            dst,
        ],
        capture_output=True, check=True,
    )
    print(f"[enhance] done → {dst}", file=sys.stderr)


if __name__ == "__main__":
    main()
