#!/usr/bin/env python3
"""Kiểm phạm vi (scope) của một giai đoạn. Chỉ dùng thư viện chuẩn.

Luật:
  S1  file nằm ngoài scope/<Gx>.json                      -> E_SCOPE_VIOLATION
  S2  fd_X import fd_Y (khác X), trừ fd_flow (nơi nối các fd) -> vi phạm R2
  S3  không đọc được scope/<Gx>.json                       -> lỗi cấu hình

Cách chạy:  python workbench/check_scope.py G0
Thoát 0 = sạch, 1 = có vi phạm.
"""
import fnmatch
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
SKIP_SUFFIX = {".pyc"}
FD_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:src\.)?(fd_[a-z_]+)")
OWN_FD = re.compile(r"^src/(fd_[a-z_]+)/")


def load_allowed(root, gate):
    data = json.loads((root / "scope" / f"{gate}.json").read_text(encoding="utf-8"))
    return list(data["allowed"])


def iter_files(root):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if set(rel.parts) & SKIP_PARTS or p.suffix in SKIP_SUFFIX:
            continue
        yield rel.as_posix(), p


def run(root=ROOT, gate="G0"):
    try:
        allowed = load_allowed(root, gate)
    except (OSError, ValueError, KeyError) as e:
        return [f"scope/{gate}.json: không đọc được ({e}) [S3]"]

    problems = []
    for rel, path in iter_files(root):
        if not any(fnmatch.fnmatch(rel, pat) for pat in allowed):
            problems.append(f"{rel} [S1] E_SCOPE_VIOLATION: file ngoài scope {gate}")
        own = OWN_FD.match(rel)
        if own and rel.endswith(".py") and own.group(1) != "fd_flow":
            text = path.read_text(encoding="utf-8", errors="replace")
            for ln, line in enumerate(text.splitlines(), 1):
                m = FD_IMPORT.match(line)
                if m and m.group(1) != own.group(1):
                    problems.append(
                        f"{rel}:{ln} [S2] {own.group(1)} import {m.group(1)} trực tiếp "
                        f"(R2: chỉ nối qua fd_flow)"
                    )
    return problems


def main(argv):
    gate = argv[0] if argv else "G0"
    problems = run(ROOT, gate)
    for p in problems:
        print(p)
    print(f"-- check_scope {gate}: vi phạm {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
