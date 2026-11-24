"""Media handler: JPEG burst storage, data-URI base64 decode, server-side MP4 stitching, safe HTTP 206."""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from server_ai.server_core import config

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")
DATA_URI_RE = re.compile(r"^data:[^;]+;base64,", re.IGNORECASE)


def safe_filename(name: str, default_ext: str = ".jpg") -> str:
    base = os.path.basename(name or "").replace("\x00", "")
    if not SAFE_NAME_RE.match(base) or base in (".", ".."):
        base = f"ev_{uuid.uuid4().hex}{default_ext}"
    return base


def _strip_data_uri(b64_data: str) -> str:
    return DATA_URI_RE.sub("", (b64_data or "").strip())


def resolve_evidence_path(rel_path: str) -> Path:
    """Resolve evidence file safely inside EVIDENCE_DIR (supports nested burst subdir or flat file)."""
    parts = [p for p in Path(rel_path.replace("\\", "/")).parts if p not in ("", ".")]
    if not parts or any(p == ".." or not SAFE_NAME_RE.match(p) for p in parts):
        raise HTTPException(status_code=400, detail="Ten file khong hop le")
    root = config.EVIDENCE_DIR.resolve()
    full = (root.joinpath(*parts)).resolve()
    if root not in full.parents:
        raise HTTPException(status_code=400, detail="Duong dan khong hop le")
    if not full.exists() or not full.is_file():
        raise HTTPException(status_code=404, detail="Khong tim thay file bang chung")
    return full


def save_base64_image(b64_data: str, vehicle_id: str, event_id: str) -> str:
    raw_b64 = _strip_data_uri(b64_data)
    try:
        raw = base64.b64decode(raw_b64, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="evidence_image base64 khong hop le")
    if not raw or len(raw) > config.MAX_UPLOAD_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Anh bang chung rong hoac vuot gioi han")
    fname = safe_filename(f"{vehicle_id}_{event_id}.jpg", ".jpg")
    (config.EVIDENCE_DIR / fname).write_bytes(raw)
    return fname


def save_binary_video(raw_bytes: bytes, vehicle_id: str, event_id: str, filename_hint: Optional[str]) -> str:
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Video rong (0 bytes)")
    if len(raw_bytes) > config.MAX_UPLOAD_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Video vuot gioi han dung luong")
    fname = safe_filename(filename_hint or f"{vehicle_id}_{event_id}.mp4", ".mp4")
    if not fname.lower().endswith(".mp4"):
        fname += ".mp4"
    (config.EVIDENCE_DIR / fname).write_bytes(raw_bytes)
    return fname


def save_base64_video(b64_data: str, vehicle_id: str, event_id: str) -> str:
    raw_b64 = _strip_data_uri(b64_data)
    try:
        raw = base64.b64decode(raw_b64, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="video_mp4_base64 khong hop le")
    return save_binary_video(raw, vehicle_id, event_id, None)


def save_burst_frames(vehicle_id: str, event_id: str, frames: List[Tuple[str, bytes]]) -> Tuple[str, List[str]]:
    """Save 10-20 JPEG keyframes sent by Pi into EVIDENCE_DIR/<burst_dir>/."""
    if len(frames) > config.MAX_BURST_FRAMES:
        raise HTTPException(status_code=413, detail=f"So anh burst vuot tran {config.MAX_BURST_FRAMES}")
    total = sum(len(b) for _, b in frames)
    if total > config.MAX_UPLOAD_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Tong dung luong burst vuot gioi han")

    dir_name = safe_filename(f"burst_{vehicle_id}_{event_id}", "")
    bdir = config.EVIDENCE_DIR / dir_name
    bdir.mkdir(parents=True, exist_ok=True)
    saved: List[str] = []
    for idx, (orig_name, content) in enumerate(frames):
        if not content:
            continue
        fname = safe_filename(f"f{idx:03d}_{orig_name}", ".jpg")
        # Normalize .jpeg/.JPG to .jpg so glob/storyboard/stitcher always match consistently (BUG-14!)
        if fname.lower().endswith((".jpeg", ".jpg")):
            fname = fname[: fname.rfind(".")] + ".jpg"
        else:
            fname += ".jpg"
        (bdir / fname).write_bytes(content)
        saved.append(f"{dir_name}/{fname}")
    return dir_name, saved


def _resolve_ffmpeg_bin() -> Optional[str]:
    """Locate ffmpeg for universal H.264 MP4 (iPhone Safari + Chrome/Edge/Android) without touching Pi deps."""
    local_bin = config.BASE_DIR / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if local_bin.exists() and local_bin.is_file():
        return str(local_bin)
    sys_bin = shutil.which("ffmpeg")
    if sys_bin:
        return sys_bin
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return str(exe)
    except Exception:
        pass
    return None


def _iter_burst_jpegs(bdir: Path) -> List[Path]:
    return sorted(p for p in bdir.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg"))


def list_burst_frame_urls(burst_dir_name: Optional[str]) -> List[str]:
    """Return sorted /evidence/<burst_dir>/<frame>.jpg URLs for Smart Storyboard human review."""
    if not burst_dir_name:
        return []
    safe_dir = safe_filename(burst_dir_name, "")
    bdir = (config.EVIDENCE_DIR / safe_dir).resolve()
    root = config.EVIDENCE_DIR.resolve()
    if root not in bdir.parents or not bdir.exists() or not bdir.is_dir():
        return []
    return [f"/evidence/{safe_dir}/{jp.name}" for jp in _iter_burst_jpegs(bdir)]


def stitch_burst_to_mp4(burst_dir_name: str, vehicle_id: str, event_id: str, fps: float = 8.0) -> Optional[str]:
    """Server-side universal browser-playable video stitching from JPEG keyframes (offloads CPU off Pi 4).

    Priority:
      1. H.264 MP4 (libx264 baseline + yuv420p + faststart) via ffmpeg / imageio-ffmpeg -> 100% iPhone Safari + all browsers.
      2. WebM VP8 (VP80) via OpenCV -> Chrome/Edge/Firefox/Android with zero extra deps.
      3. Legacy mp4v MP4 fallback only if neither is available.
    """
    if not config.SERVER_STITCH_MP4:
        return None
    bdir = config.EVIDENCE_DIR / burst_dir_name
    if not bdir.exists():
        return None
    jpgs = _iter_burst_jpegs(bdir)
    if len(jpgs) < 2:
        return None

    eff_fps = max(1.0, float(fps))
    ffmpeg_bin = _resolve_ffmpeg_bin()
    if ffmpeg_bin:
        mp4_name = safe_filename(f"{vehicle_id}_{event_id}_stitched.mp4", ".mp4")
        out_path = config.EVIDENCE_DIR / mp4_name
        list_file = bdir / "_ffmpeg_list.txt"
        try:
            dur = 1.0 / eff_fps
            lines = []
            for jp in jpgs:
                safe_p = str(jp.resolve()).replace("\\", "/").replace("'", "'\\''")
                lines.append(f"file '{safe_p}'\nduration {dur:.4f}\n")
            last_p = str(jpgs[-1].resolve()).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{last_p}'\n")
            list_file.write_text("".join(lines), encoding="utf-8")
            proc = subprocess.run(
                [
                    ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-r", f"{eff_fps:.2f}", str(out_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                return mp4_name
            out_path.unlink(missing_ok=True)
        except Exception:
            out_path.unlink(missing_ok=True)
        finally:
            try:
                if list_file.exists():
                    list_file.unlink()
            except Exception:
                pass

    try:
        import cv2
    except Exception:
        return None
    first = None
    for jp in jpgs:
        cand = cv2.imread(str(jp))
        if cand is not None and cand.size > 0:
            first = cand
            break
    if first is None:
        return None
    h, w = first.shape[:2]

    for fourcc_str, ext in (("VP80", ".webm"), ("mp4v", ".mp4")):
        vid_name = safe_filename(f"{vehicle_id}_{event_id}_stitched{ext}", ext)
        out_path = config.EVIDENCE_DIR / vid_name
        writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*fourcc_str), eff_fps, (w, h))
        if not writer.isOpened():
            writer.release()
            out_path.unlink(missing_ok=True)
            continue
        try:
            for jp in jpgs:
                img = cv2.imread(str(jp))
                if img is None:
                    continue
                if img.shape[1] != w or img.shape[0] != h:
                    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
                writer.write(img)
        finally:
            writer.release()
        if out_path.exists() and out_path.stat().st_size > 0:
            return vid_name
        out_path.unlink(missing_ok=True)
    return None


def _iter_range(path: Path, start: int, end: int, chunk_size: int = 1024 * 256):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def serve_evidence_file(rel_path: str, range_header: Optional[str]):
    path = resolve_evidence_path(rel_path)
    file_size = path.stat().st_size
    suf = path.suffix.lower()
    content_type = "video/webm" if suf == ".webm" else ("video/mp4" if suf == ".mp4" else "image/jpeg")

    if file_size == 0:
        return StreamingResponse(iter(()), status_code=200, media_type=content_type, headers={"Content-Length": "0"})

    if not range_header:
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size)}
        return StreamingResponse(_iter_range(path, 0, file_size - 1), media_type=content_type, headers=headers)

    m = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
    if not m:
        raise HTTPException(status_code=416, detail="Range header khong hop le")
    start_s, end_s = m.groups()
    if not start_s and not end_s:
        raise HTTPException(status_code=416, detail="Range khong hop le")
    if not start_s and end_s:
        # Suffix byte range: bytes=-500
        suffix = int(end_s)
        if suffix <= 0:
            raise HTTPException(status_code=416, detail="Range khong hop le")
        start = max(0, file_size - suffix)
        end = file_size - 1
    else:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        end = min(end, file_size - 1)

    if start > end or start >= file_size:
        raise HTTPException(
            status_code=416,
            detail="Range khong hop le",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(
        _iter_range(path, start, end),
        status_code=206,
        media_type=content_type,
        headers=headers,
    )
