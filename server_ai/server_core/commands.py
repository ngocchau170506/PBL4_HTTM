"""Two-way command lifecycle (PENDING -> SENT_TO_PI -> EXECUTED_OK/FAILED) + SQLite persistent remote_config."""
from __future__ import annotations

import re
import time
import uuid
from typing import Optional

from fastapi import HTTPException

from server_ai.server_core import config, database
from server_ai.server_core.sse_broker import broker

ALERT_RE = re.compile(r"^ALERT:[12]:(\d{2,4})$")
DEFAULT_REMOTE_CONFIG = {"allow_smoking": True, "allow_phone": True}


def validate_command(cmd: str) -> bool:
    cmd = (cmd or "").strip()
    if cmd in config.COMMAND_WHITELIST_EXACT or cmd in config.COMMAND_WHITELIST_NEW:
        return True
    if cmd.startswith("VOICE:"):
        try:
            n = int(cmd.split(":", 1)[1])
            return 1 <= n <= 9
        except Exception:
            return False
    if cmd.startswith("VOL:"):
        try:
            v = int(cmd.split(":", 1)[1])
            return 10 <= v <= 100
        except Exception:
            return False
    m = ALERT_RE.match(cmd)
    if m:
        val = int(m.group(1))
        return 50 <= val <= 5000
    return False


def enqueue_command(vehicle_id: str, cmd: str, source: str, actor_role: str) -> dict:
    cmd_clean = (cmd or "").strip()
    if not validate_command(cmd_clean):
        raise HTTPException(status_code=400, detail=f"Lenh '{cmd_clean}' khong nam trong whitelist")
    cmd_uid = f"CMD_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    with database.tx() as conn:
        conn.execute(
            "INSERT INTO commands (cmd_uid, vehicle_id, command, source, actor_role, status) "
            "VALUES (?,?,?,?,?,'PENDING')",
            (cmd_uid, vehicle_id, cmd_clean, source, actor_role),
        )
    broker.publish("command_queued", {"cmd_uid": cmd_uid, "vehicle_id": vehicle_id, "command": cmd_clean})
    return {"cmd_uid": cmd_uid, "vehicle_id": vehicle_id, "command": cmd_clean, "status": "PENDING"}


def pending_commands_for(vehicle_id: str, mark_sent: bool = True) -> list:
    """Atomically fetch & claim PENDING commands inside BEGIN IMMEDIATE tx (prevents duplicate delivery!)."""
    with database.tx() as c:
        rows = database.rows_to_list(
            c.execute(
                "SELECT cmd_uid, command FROM commands WHERE vehicle_id=? AND status='PENDING' ORDER BY created_at",
                (vehicle_id,),
            ).fetchall()
        )
        if mark_sent and rows:
            now_s = time.time()
            c.executemany(
                "UPDATE commands SET status='SENT_TO_PI', delivered_at=? WHERE cmd_uid=? AND status='PENDING'",
                [(now_s, r["cmd_uid"]) for r in rows],
            )
    return rows


def ack_command(cmd_uid: str, result: str, detail: Optional[str] = None) -> dict:
    if not cmd_uid:
        raise HTTPException(status_code=400, detail="Thieu cmd_uid")
    status = "EXECUTED_OK" if str(result).upper() in ("OK", "EXECUTED_OK", "SUCCESS", "TRUE") else "FAILED"
    with database.tx() as conn:
        cur = conn.execute(
            "UPDATE commands SET status=?, ack_detail=?, acked_at=? WHERE cmd_uid=?",
            (status, detail or "", time.time(), cmd_uid),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Khong tim thay cmd_uid {cmd_uid}")
    broker.publish("command_ack", {"cmd_uid": cmd_uid, "status": status, "detail": detail or ""})
    return {"ok": True, "cmd_uid": cmd_uid, "status": status}


def list_commands(vehicle_id: Optional[str] = None, limit: int = 200) -> list:
    conn = database.get_conn()
    if vehicle_id:
        rows = conn.execute(
            "SELECT * FROM commands WHERE vehicle_id=? ORDER BY created_at DESC LIMIT ?", (vehicle_id, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM commands ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return database.rows_to_list(rows)


def set_remote_config(vehicle_id: str, patch: dict) -> dict:
    import math
    cfg = get_remote_config(vehicle_id)
    for k, v in (patch or {}).items():
        if k in ("allow_smoking", "allow_phone"):
            cfg[k] = bool(v)
        elif k in ("smoking_conf_threshold", "phone_conf_threshold"):
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"Gia tri {k} khong hop le")
            if not math.isfinite(fv) or not (0.1 <= fv <= 0.95):
                raise HTTPException(status_code=400, detail=f"{k} phai nam trong [0.1, 0.95]")
            cfg[k] = fv
        elif k in ("eye_threshold_seconds", "yawn_threshold_seconds"):
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"Gia tri {k} khong hop le")
            if not math.isfinite(fv) or not (0.5 <= fv <= 10.0):
                raise HTTPException(status_code=400, detail=f"{k} phai nam trong [0.5, 10.0]")
            cfg[k] = fv
        elif k == "drowsy_window_s":
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="drowsy_window_s khong hop le")
            if not math.isfinite(fv) or not (120.0 <= fv <= 240.0):
                raise HTTPException(status_code=400, detail="drowsy_window_s phai nam trong [120, 240] giay (2-4 phut)")
            cfg[k] = fv
        elif k == "drowsy_escalation_limit":
            try:
                iv = int(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="drowsy_escalation_limit khong hop le")
            if not (2 <= iv <= 5):
                raise HTTPException(status_code=400, detail="drowsy_escalation_limit phai nam trong [2, 5]")
            cfg[k] = iv
        elif k == "absence_alert_s":
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="absence_alert_s khong hop le")
            if not math.isfinite(fv) or not (15.0 <= fv <= 30.0):
                raise HTTPException(status_code=400, detail="absence_alert_s phai nam trong [15, 30] giay")
            cfg[k] = fv
        elif k == "pause_duration_min":
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="pause_duration_min khong hop le")
            if not math.isfinite(fv) or not (5.0 <= fv <= 60.0):
                raise HTTPException(status_code=400, detail="pause_duration_min phai nam trong [5, 60] phut")
            cfg[k] = fv
        elif k == "auto_resume_speed_kmh":
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="auto_resume_speed_kmh khong hop le")
            if not math.isfinite(fv) or not (5.0 <= fv <= 40.0):
                raise HTTPException(status_code=400, detail="auto_resume_speed_kmh phai nam trong [5, 40] km/h")
            cfg[k] = fv
    with database.tx() as conn:
        conn.execute(
            """INSERT INTO remote_configs (vehicle_id, config_json, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(vehicle_id) DO UPDATE SET config_json=excluded.config_json, updated_at=excluded.updated_at""",
            (vehicle_id, database.dumps(cfg), time.time()),
        )
    broker.publish("remote_config_update", {"vehicle_id": vehicle_id, "config": cfg})
    return cfg


def get_remote_config(vehicle_id: str) -> dict:
    conn = database.get_conn()
    row = conn.execute("SELECT config_json FROM remote_configs WHERE vehicle_id=?", (vehicle_id,)).fetchone()
    if row:
        cfg = dict(DEFAULT_REMOTE_CONFIG)
        cfg.update(database.loads(row["config_json"]))
        return cfg
    return dict(DEFAULT_REMOTE_CONFIG)
