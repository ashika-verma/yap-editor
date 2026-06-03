#!/usr/bin/env python3
"""
Audio enhancement pipeline.
Usage: python enhance.py <video_or_wav_path> <output_wav_path>

Pipeline:
  1. resemble-enhance denoiser (neural noise suppression)
  2. EQ: highpass, mud cut @ 275Hz, box cut @ 650Hz, presence @ 2.5kHz, de-ess @ 6.5kHz
  3. Harmonic exciter (warmth)
  4. Noise gate ratio=1.5 (gentle expander, reduces silences without hard-cutting)
  5. Compressor (2:1, slow attack/release)
  6. loudnorm -16 LUFS
"""
import sys
import os
import subprocess
import tempfile


def main():
    if len(sys.argv) < 3:
        print("Usage: enhance.py <input> <output_wav>", file=sys.stderr)
        sys.exit(1)

    src = sys.argv[1]
    dst = sys.argv[2]

    with tempfile.TemporaryDirectory() as tmp:
        raw_wav = os.path.join(tmp, "raw.wav")
        denoised_wav = os.path.join(tmp, "denoised.wav")

        # Step 1: extract raw audio
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vn", "-ar", "44100", "-ac", "1", raw_wav],
            capture_output=True, check=True,
        )

        # Step 2: resemble-enhance denoise
        print("[enhance] denoising...", file=sys.stderr)
        import torch, torchaudio
        from resemble_enhance.enhancer.inference import denoise

        wav, sr = torchaudio.load(raw_wav)
        wav1d = wav.squeeze(0)
        denoised, out_sr = denoise(wav1d, sr, "cpu")
        torchaudio.save(denoised_wav, denoised.unsqueeze(0).cpu(), out_sr)

        # Step 3: EQ + exciter + gate + compressor + loudnorm
        print("[enhance] EQ + dynamics...", file=sys.stderr)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", denoised_wav,
                "-af",
                "highpass=f=80,"
                "equalizer=f=275:width_type=o:width=2:g=-4,"
                "equalizer=f=650:width_type=o:width=1.5:g=-2.5,"
                "equalizer=f=2500:width_type=o:width=2:g=1.5,"
                "equalizer=f=6500:width_type=o:width=1:g=-2,"
                "aexciter=level_in=1:level_out=1:amount=1.5:drive=3:blend=0,"
                "agate=threshold=0.005:ratio=1.5:attack=10:release=200,"
                "acompressor=threshold=-10dB:ratio=2:attack=10:release=150,"
                "loudnorm=I=-18:TP=-1.5:LRA=11",
                "-ar", "48000", "-ac", "1", dst,
            ],
            capture_output=True, check=True,
        )

    print(f"[enhance] done → {dst}", file=sys.stderr)


if __name__ == "__main__":
    main()
