#!/usr/bin/env python3
"""CỔNG KIỂM DUY NHẤT trước REPORT và MERGE. Chỉ dùng thư viện chuẩn.

Chạy:  python workbench/gate.py
Thứ tự: giai đoạn hiện tại (feature-map.md) -> scope -> comment (F7/F8) -> unittest.
Chỉ thoát 0 khi TẤT CẢ đều sạch.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "workbench"


def current_gate():
    text = (ROOT / "feature-map.md").read_text(encoding="utf-8")
    m = re.search(r"GIAI ĐOẠN HIỆN TẠI:\s*(G\d+)", text)
    return m.group(1) if m else None


def step(name, cmd):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout + r.stderr).strip()
    print(f"== {name}: {'PASS' if r.returncode == 0 else 'FAIL'}")
    if out:
        print(out)
    return r.returncode == 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    gate = current_gate()
    if not gate:
        print("FAIL: feature-map.md thiếu dòng 'GIAI ĐOẠN HIỆN TẠI: Gx'")
        return 1
    py = sys.executable
    results = [
        step(f"scope {gate}", [py, "-I", "-X", "utf8", str(WB / "check_scope.py"), gate]),
        step("comment (src/)", [py, "-I", "-X", "utf8", str(WB / "check_comments.py"), str(ROOT / "src")]),
        step("unittest workbench/tests", [py, "-I", "-X", "utf8", "-m", "unittest", "discover", "-s", str(WB / "tests")]),
    ]
    ok = all(results)
    print("GATE CHECK:", "SẠCH" if ok else "CÓ VI PHẠM — không được báo REPORT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
