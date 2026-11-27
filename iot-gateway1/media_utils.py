"""Tiện ích dùng chung xử lý lưu trữ ảnh JPEG & video MP4 vi phạm kèm phát HTTP Byte-Range."""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
import re
import time
from typing import Any, Dict, Tuple


def _safe_stem(raw_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw_id or "")).strip("._")
    return cleaned[:48] or f"EVT_{int(time.time() * 1000)}"


def _decode_b64_payload(raw_b64: str) -> bytes:
    text = str(raw_b64 or "").strip()
    if not text:
        return b""
    if "," in text and text.lstrip().startswith("data:"):
        text = text.split(",", 1)[1]
    return base64.b64decode(text)


def extract_and_save_violation_media(
    payload: Dict[str, Any],
    evidence_dir: Path,
) -> Tuple[str, str, str]:
    """Tách ảnh JPEG & video MP4 (.mp4/.avi) từ payload /api/v1/violation và lưu ra ổ đĩa.

    Xóa chuỗi Base64 video kích thước lớn khỏi payload sau khi lưu để tránh tràn RAM/log/SQLite,
    đồng thời gắn lại `video_url` và `image_url` nhẹ để Web Dashboard phát trực tiếp.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)
    ts_ms = int(time.time() * 1000)
    stem = _safe_stem(payload.get("event_id") or f"VIOLATION_{ts_ms}")

    saved_img_path = ""
    saved_vid_path = ""
    video_url = ""

    # 1. Ảnh bằng chứng JPEG
    img_raw = payload.get("evidence_image") or payload.get("image_base64") or ""
    if isinstance(img_raw, str) and img_raw.strip():
        try:
            img_bytes = _decode_b64_payload(img_raw)
            if img_bytes:
                img_name = f"{stem}_{ts_ms}.jpg"
                img_fpath = evidence_dir / img_name
                img_fpath.write_bytes(img_bytes)
                saved_img_path = str(img_fpath)
                payload["image_url"] = f"/evidence/{img_name}"
        except Exception as exc:
            print(f"[MEDIA] Lỗi lưu ảnh bằng chứng: {exc}")

    # 2. Video 10 giây MP4 từ IOTv4 (nằm trong meta.video_mp4_base64 hoặc root)
    meta = payload.get("meta")
    vid_raw = ""
    if isinstance(meta, dict) and meta.get("video_mp4_base64"):
        vid_raw = str(meta.pop("video_mp4_base64"))
    elif payload.get("video_mp4_base64"):
        vid_raw = str(payload.pop("video_mp4_base64"))
    elif payload.get("video_base64"):
        vid_raw = str(payload.pop("video_base64"))

    if vid_raw.strip():
        try:
            vid_bytes = _decode_b64_payload(vid_raw)
            if vid_bytes:
                is_avi = vid_bytes[:4] == b"RIFF"
                ext = ".avi" if is_avi else ".mp4"
                vid_name = f"{stem}_{ts_ms}{ext}"
                vid_fpath = evidence_dir / vid_name
                tmp_fpath = evidence_dir / f".{vid_name}.tmp"
                tmp_fpath.write_bytes(vid_bytes)
                tmp_fpath.replace(vid_fpath)
                saved_vid_path = str(vid_fpath)
                video_url = f"/evidence/{vid_name}"
                payload["video_url"] = video_url
                payload["video_filename"] = vid_name
                payload["video_size_bytes"] = len(vid_bytes)
                if isinstance(meta, dict):
                    meta["video_url"] = video_url
                    meta["video_filename"] = vid_name
                    meta["video_size_bytes"] = len(vid_bytes)
        except Exception as exc:
            print(f"[MEDIA] Lỗi lưu video MP4 bằng chứng: {exc}")

    return saved_img_path, saved_vid_path, video_url


def serve_evidence_file(handler: Any, evidence_dir: Path, raw_path: str) -> None:
    """Phục vụ file ảnh/video trong evidence/ kèm hỗ trợ HTTP Byte-Range (206) để tua video MP4."""
    clean_path = raw_path.split("?", 1)[0]
    fname = Path(clean_path.replace("/evidence/", "", 1)).name
    if not fname:
        handler.send_response(404)
        handler.end_headers()
        return

    fpath = (evidence_dir / fname).resolve()
    try:
        fpath.relative_to(evidence_dir.resolve())
    except ValueError:
        handler.send_response(403)
        handler.end_headers()
        return

    if not fpath.exists() or not fpath.is_file():
        handler.send_response(404)
        handler.end_headers()
        return

    total_size = fpath.stat().st_size
    if fpath.suffix.lower() == ".mp4":
        mime = "video/mp4"
    elif fpath.suffix.lower() == ".avi":
        mime = "video/x-msvideo"
    else:
        mime, _ = mimetypes.guess_type(str(fpath))
        mime = mime or "application/octet-stream"

    range_header = handler.headers.get("Range", "") if handler.headers else ""
    start = 0
    end = total_size - 1
    status_code = 200

    if range_header.startswith("bytes=") and total_size > 0:
        spec = range_header.replace("bytes=", "", 1).split(",", 1)[0].strip()
        if "-" in spec:
            s_part, e_part = spec.split("-", 1)
            try:
                if s_part:
                    start = int(s_part)
                    end = int(e_part) if e_part else total_size - 1
                elif e_part:
                    suffix_len = int(e_part)
                    start = max(0, total_size - suffix_len)
                    end = total_size - 1
                if start < 0 or start >= total_size or end < start:
                    handler.send_response(416)
                    handler.send_header("Content-Range", f"bytes */{total_size}")
                    handler.end_headers()
                    return
                end = min(end, total_size - 1)
                status_code = 206
            except ValueError:
                start = 0
                end = total_size - 1
                status_code = 200

    content_len = max(0, end - start + 1) if total_size > 0 else 0
    try:
        handler.send_response(status_code)
        handler.send_header("Content-Type", mime)
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Access-Control-Allow-Origin", "*")
        if status_code == 206:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        handler.send_header("Content-Length", str(content_len))
        handler.end_headers()

        if content_len > 0:
            with fpath.open("rb") as fh:
                fh.seek(start)
                remaining = content_len
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    remaining -= len(chunk)
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        pass
