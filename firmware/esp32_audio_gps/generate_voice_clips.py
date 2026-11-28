"""Generate firmware/esp32_audio_gps/voice_clips.h (16 kHz mono G.711 u-law 8-bit PROGMEM).

Uses Microsoft Edge Neural Vietnamese TTS (`edge_tts`, vi-VN-HoaiMyNeural) + `imageio_ffmpeg`
to synthesize 7 crystal-clear cabin alerts normalized to -1 dBFS for MAX98357A @ 80% volume:
  [1] DROWSY    : "Cảnh báo! Bạn có dấu hiệu buồn ngủ, vui lòng tỉnh táo khi lái xe."
  [2] YAWN      : "Bạn có dấu hiệu ngáp dài, vui lòng tỉnh táo khi lái xe."
  [3] DISTRACT  : "Bạn đang mất tập trung khi lái xe."
  [4] CO2_WARN  : "Không khí trong xe đang bí, vui lòng mở cửa sổ để thông thoáng."
  [5] CO2_CRIT  : "Cảnh báo! Nồng độ khí CO2 trong xe rất cao, vui lòng mở cửa sổ ngay."
  [6] PHONE     : "Cảnh báo! Bạn đang sử dụng điện thoại, vui lòng tập trung lái xe."
  [7] SMOKING   : "Cảnh báo! Phát hiện hút thuốc trên xe, vui lòng không hút thuốc khi lái xe."
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

import imageio_ffmpeg
import numpy as np
import requests

SAMPLE_RATE = 16000
VOICE_NAME = "vi-VN-HoaiMyNeural"
VOICE_RATE = "+8%"  # Slightly brisk & crisp for cabin alerts while saving Flash space

CLIPS: List[Tuple[int, str, str]] = [
    (1, "DROWSY", "Cảnh báo, bạn có dấu hiệu buồn ngủ, vui lòng tỉnh táo khi lái xe."),
    (2, "YAWN", "Bạn có dấu hiệu ngáp dài, vui lòng tỉnh táo khi lái xe."),
    (3, "DISTRACT", "Bạn đang mất tập trung khi lái xe."),
    (4, "CO2_WARN", "Không khí trong xe đang bí, vui lòng mở cửa sổ để thông thoáng."),
    (5, "CO2_CRIT", "Cảnh báo, nồng độ khí CO2 trong xe rất cao, vui lòng mở cửa sổ ngay."),
    (6, "PHONE", "Cảnh báo, bạn đang sử dụng điện thoại, vui lòng tập trung lái xe."),
    (7, "SMOKING", "Cảnh báo, phát hiện hút thuốc trên xe, vui lòng không hút thuốc khi lái xe."),
]


def pcm16_to_ulaw(samples: np.ndarray) -> bytes:
    """Standard ITU-T G.711 u-law encoder matching esp32_audio_gps.ino `ulawDecode()`."""
    CLIP = 32635
    BIAS = 0x84
    out = bytearray(len(samples))
    for i, s in enumerate(samples.tolist()):
        sign = 0x80 if s < 0 else 0x00
        mag = -s if s < 0 else s
        if mag > CLIP:
            mag = CLIP
        mag += BIAS
        exp = 7
        mask = 0x4000
        while exp > 0 and not (mag & mask):
            exp -= 1
            mask >>= 1
        mant = (mag >> (exp + 3)) & 0x0F
        out[i] = (~(sign | (exp << 4) | mant)) & 0xFF
    return bytes(out)


def _synth_mp3(text: str, out_mp3: Path) -> str:
    """Try Microsoft Edge Neural TTS first; fallback cleanly to Google Vietnamese TTS."""
    try:
        import edge_tts

        async def _run() -> None:
            comm = edge_tts.Communicate(text=text, voice=VOICE_NAME, rate=VOICE_RATE)
            await comm.save(str(out_mp3))

        asyncio.run(_run())
        if out_mp3.exists() and out_mp3.stat().st_size > 512:
            return "edge_tts"
    except Exception:
        pass

    r = requests.get(
        "https://translate.google.com/translate_tts",
        params={"ie": "UTF-8", "q": text, "tl": "vi", "client": "tw-ob"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    r.raise_for_status()
    if len(r.content) < 512:
        raise RuntimeError(f"TTS returned empty audio for: {text}")
    out_mp3.write_bytes(r.content)
    return "google_tts"


def _mp3_to_pcm16_trimmed(mp3_path: Path, ffmpeg_exe: str) -> np.ndarray:
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(mp3_path),
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    pcm = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32)
    if pcm.size == 0:
        raise RuntimeError(f"Empty PCM decoded from {mp3_path}")
    # Trim leading/trailing silence (< 1.5% peak) keeping 40ms clean padding
    peak = float(np.max(np.abs(pcm)))
    if peak > 10.0:
        nz = np.where(np.abs(pcm) > peak * 0.015)[0]
        if nz.size > 0:
            pad = int(SAMPLE_RATE * 0.04)
            s0 = max(0, int(nz[0]) - pad)
            s1 = min(pcm.size, int(nz[-1]) + pad)
            pcm = pcm[s0:s1]
        # Normalize to -1 dBFS (~29200) for crisp speech at 80% hardware gain
        pcm = np.clip(pcm * (29200.0 / float(np.max(np.abs(pcm)))), -32767.0, 32767.0)
    return pcm.astype(np.int16)


def generate_header(target_path: Path) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    encoded: List[Tuple[int, str, str, bytes]] = []

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        for cid, tag, text in CLIPS:
            mp3_file = tmp_dir / f"clip_{cid}.mp3"
            engine = _synth_mp3(text, mp3_file)
            pcm = _mp3_to_pcm16_trimmed(mp3_file, ffmpeg_exe)
            ulaw = pcm16_to_ulaw(pcm)
            encoded.append((cid, tag, text, ulaw))
            print(f"  [{cid}] {tag:9s} ({engine}) -> {len(ulaw):6d} bytes ({len(ulaw) / SAMPLE_RATE:.2f}s)")

    total_bytes = sum(len(x[3]) for x in encoded)
    lines: List[str] = [
        "// =====================================================================",
        "// FILE TU DONG SINH BOI firmware/esp32_audio_gps/generate_voice_clips.py",
        "// Am thanh: 16000 Hz, mono, G.711 u-law 8-bit (7 clips, -1 dBFS)",
        f"// Tong dung luong PROGMEM: {total_bytes} bytes ({total_bytes / 1024.0:.1f} KB)",
        "// =====================================================================",
        "#pragma once",
        "#include <Arduino.h>",
        "",
        f"#define VOICE_SAMPLE_RATE {SAMPLE_RATE}",
        f"#define VOICE_CLIP_COUNT  {len(encoded)}",
        "",
    ]

    for cid, tag, text, ulaw in encoded:
        dur = len(ulaw) / float(SAMPLE_RATE)
        lines.append(f'// [{cid}] {tag}: "{text}" ({dur:.2f}s)')
        lines.append(f"static const uint8_t VOICE_DATA_{cid}[{len(ulaw)}] PROGMEM = {{")
        for i in range(0, len(ulaw), 24):
            chunk = ",".join(str(b) for b in ulaw[i : i + 24])
            comma = "," if (i + 24) < len(ulaw) else ""
            lines.append(f"  {chunk}{comma}")
        lines.append("};")
        lines.append("")

    ptr_list = ", ".join(f"VOICE_DATA_{cid}" for cid, _, _, _ in encoded)
    len_list = ", ".join(str(len(ulaw)) for _, _, _, ulaw in encoded)
    lines.append(f"static const uint8_t* const VOICE_CLIPS[VOICE_CLIP_COUNT] = {{ {ptr_list} }};")
    lines.append(f"static const uint32_t VOICE_LENS[VOICE_CLIP_COUNT] = {{ {len_list} }};")
    lines.append("")

    target_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {target_path} ({total_bytes / 1024.0:.1f} KB PROGMEM, {len(encoded)} clips)")


if __name__ == "__main__":
    out_h = Path(__file__).resolve().parent / "voice_clips.h"
    generate_header(out_h)
