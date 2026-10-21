"""Bộ chạy thử độc lập từng node (FEAT-004). Chỉ chạy trên PC, chỉ dùng thư viện chuẩn.

Nhận một đối tượng tuân theo NodeContract (node_id, health(), self_test()), kiểm đúng giao diện
rồi trả báo cáo dict. Không nuốt lỗi: ngoại lệ của node được ghi vào báo cáo cùng kiểu và nội dung.
Cách dùng:  from probes.probe_runner import run_probe, run_all
"""
from __future__ import annotations

import json
import sys
import time
import typing
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contracts import logic as L  # noqa: E402

KNOWN_NODES = set(typing.get_args(L.NodeId))
KNOWN_HEALTH = set(typing.get_args(L.Health))


def _now_ms() -> int:
    return int(time.time() * 1000)


def run_probe(node: Any) -> Dict[str, Any]:
    """Chạy health() và self_test() của một node; mọi sai lệch giao diện thành mục trong 'problems'."""
    problems: List[str] = []
    node_id = getattr(node, "node_id", None)
    if node_id not in KNOWN_NODES:
        problems.append(f"node_id {node_id!r} không nằm trong NodeId của contracts")

    health = None
    try:
        health = node.health()
    except Exception as e:
        problems.append(f"health() ném {type(e).__name__}: {e}")
    else:
        if health not in KNOWN_HEALTH:
            problems.append(f"health() trả {health!r}, cần một trong {sorted(KNOWN_HEALTH)}")

    ok, detail = False, ""
    try:
        result = node.self_test()
    except Exception as e:
        problems.append(f"self_test() ném {type(e).__name__}: {e}")
    else:
        if (not isinstance(result, tuple) or len(result) != 2
                or not isinstance(result[0], bool) or not isinstance(result[1], str)):
            problems.append(f"self_test() phải trả (bool, str), đang trả {result!r}")
        else:
            ok, detail = result

    return {
        "node_id": node_id,
        "ts_ms": _now_ms(),
        "health": health,
        "self_test_ok": ok,
        "detail": detail,
        "problems": problems,
        "pass": ok and not problems and health != "DOWN",
    }


def run_all(nodes: Iterable[Any]) -> Dict[str, Any]:
    reports = [run_probe(n) for n in nodes]
    return {"ts_ms": _now_ms(), "pass": all(r["pass"] for r in reports), "reports": reports}


def main(argv: List[str]) -> int:
    print(json.dumps({"error": "chưa có node thật để chạy thử; dùng run_probe(node) từ test hoặc probe riêng",
                      "args": argv}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
