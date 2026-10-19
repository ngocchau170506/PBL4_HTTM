#!/usr/bin/env python3
"""Kiểm comment: chặn việc dùng comment để đi đường vòng. Chỉ dùng thư viện chuẩn.

Cách chạy:  python workbench/check_comments.py [đường_dẫn ...]   (mặc định quét src/)
Thoát 0 = sạch, 1 = có vi phạm.

Luật:
  C1  comment tắt kiểm tra (@ts-ignore, eslint-disable, noqa, type: ignore, ...)
  C2  comment giữ chỗ thay cho code (TODO, FIXME, "...", "làm sau", ...)
  C3  code bị comment lại
  C4  hàm/khối chỉ có comment hoặc `pass`
  C5  catch / except rỗng (nuốt lỗi)

Ngoại lệ chỉ MAIN ghi vào workbench/comment_allowlist.txt, dạng: đường_dẫn|LUẬT|lý_do
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILE = ROOT / "workbench" / "comment_allowlist.txt"
CSTYLE = {".ts", ".tsx", ".js", ".jsx", ".mjs"}
PYSTYLE = {".py"}
YAML = {".yaml", ".yml"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "out"}

SUPPRESS = re.compile(
    r"@ts-(ignore|expect-error|nocheck)|eslint-(disable|enable)|\bnoqa\b|type:\s*ignore"
    r"|pylint:\s*disable|pragma:\s*no cover|istanbul\s+ignore|prettier-ignore|\bnosec\b|fmt:\s*(off|skip)",
    re.I,
)
PLACEHOLDER = re.compile(
    r"\b(TODO|FIXME|HACK|XXX|TBD|WIP)\b|placeholder|implement\s+(later|me)|làm sau|sẽ làm|giữ chỗ",
    re.I,
)
ELLIPSIS_END = re.compile(r"(\.\.\.|…)\s*$")
CODE_START = re.compile(
    r"^(return\b|const\s|let\s|var\s|function\b|import\s|from\s+\S+\s+import\b"
    r"|export\s+(const|function|default|async)|if\s*\(|for\s*\(|while\s*\(|else\s*\{?$"
    r"|def\s+\w+\(|class\s+\w+|print\(|console\.\w+\(|await\s|throw\s|try\s*\{|except\b.*:$)"
)
CODE_END = re.compile(r"[;{}]$")
CALL_ONLY = re.compile(r"^[\w.]+\(.*\)$")

EMPTY_BODY = re.compile(
    r"\)\s*(?::\s*[^{};=()]+?)?\s*(?:=>\s*)?\{\s*(?:(?://[^\n]*|/\*.*?\*/)\s*)+\}", re.S
)
EMPTY_CATCH = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?:(?://[^\n]*|/\*.*?\*/)\s*)*\}", re.S)
EMPTY_EXCEPT = re.compile(r"\bexcept\b[^\n]*:\s*(?:#[^\n]*\n\s*)*pass\b")


def mask(seg):
    return "".join("\n" if ch == "\n" else "_" for ch in seg)


def scan_cstyle(text):
    out, comments = [], []
    i, n, line = 0, len(text), 1
    while i < n:
        c, two = text[i], text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j == -1 else j
            comments.append((line, text[i + 2:j].strip()))
            out.append(text[i:j])
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            block = text[i:j]
            for k, ln in enumerate(block.split("\n")):
                t = re.sub(r"^(/\*+|\*+/?|\*)\s*", "", ln.strip())
                t = re.sub(r"\s*\*+/$", "", t).strip()
                if t:
                    comments.append((line + k, t))
            out.append(block)
            line += block.count("\n")
            i = j
        elif c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n" and c != "`":
                    break
                j += 1
            j = min(j, n - 1)
            seg = text[i:j + 1]
            out.append(c + mask(seg[1:-1]) + (seg[-1] if len(seg) > 1 else ""))
            line += seg.count("\n")
            i = j + 1
        else:
            out.append(c)
            if c == "\n":
                line += 1
            i += 1
    return comments, "".join(out)


def scan_py(text):
    out, comments = [], []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "#":
            j = text.find("\n", i)
            j = n if j == -1 else j
            comments.append((line, text[i + 1:j].strip()))
            out.append(text[i:j])
            i = j
        elif text[i:i + 3] in ('"""', "'''"):
            q = text[i:i + 3]
            j = text.find(q, i + 3)
            j = n if j == -1 else j + 3
            seg = text[i:j]
            out.append(q + mask(seg[3:-3]) + q)
            line += seg.count("\n")
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                if text[j] == "\\":
                    j += 1
                j += 1
            j = min(j, n - 1)
            seg = text[i:j + 1]
            out.append(c + mask(seg[1:-1]) + (seg[-1] if len(seg) > 1 else ""))
            line += seg.count("\n")
            i = j + 1
        else:
            out.append(c)
            if c == "\n":
                line += 1
            i += 1
    return comments, "".join(out)


def looks_like_code(t):
    return bool(CODE_START.search(t) or CODE_END.search(t) or CALL_ONLY.match(t))


def check_comment_texts(comments, add):
    for ln, t in comments:
        if ln == 1 and t.startswith("!"):
            continue
        if SUPPRESS.search(t):
            add(ln, "C1", f"comment tắt kiểm tra: {t[:60]}")
        elif PLACEHOLDER.search(t) or ELLIPSIS_END.search(t):
            add(ln, "C2", f"comment giữ chỗ thay cho code: {t[:60]}")
        elif looks_like_code(t):
            add(ln, "C3", f"giống code bị comment lại: {t[:60]}")


def line_of(masked, pos):
    return masked.count("\n", 0, pos) + 1


def check_cstyle_structure(masked, add):
    for m in EMPTY_BODY.finditer(masked):
        add(line_of(masked, m.start()), "C4", "khối chỉ có comment, không có code")
    for m in EMPTY_CATCH.finditer(masked):
        add(line_of(masked, m.start()), "C5", "catch rỗng hoặc chỉ có comment (nuốt lỗi)")


def check_py_structure(masked, add):
    for m in EMPTY_EXCEPT.finditer(masked):
        add(line_of(masked, m.start()), "C5", "except chỉ có pass (nuốt lỗi)")
    lines = masked.split("\n")
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not (s.startswith("def ") or s.startswith("async def ")) or not s.endswith(":"):
            continue
        base = len(raw) - len(raw.lstrip())
        has_comment = has_pass = real = False
        for nxt in lines[idx + 1:]:
            t = nxt.strip()
            if not t:
                continue
            if len(nxt) - len(nxt.lstrip()) <= base:
                break
            if t.startswith("#"):
                has_comment = True
            elif t == "pass":
                has_pass = True
            elif t == "...":
                continue
            else:
                real = True
                break
        if not real and (has_comment or has_pass):
            add(idx + 1, "C4", "hàm chỉ có comment hoặc pass, không có code")


def load_allow():
    allowed = set()
    if ALLOW_FILE.exists():
        for ln in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                parts = [p.strip() for p in ln.split("|")]
                if len(parts) >= 3 and parts[2]:
                    allowed.add((parts[0], parts[1]))
    return allowed


def rel(p):
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def iter_files(targets):
    for t in targets:
        t = Path(t)
        if t.is_file():
            yield t
        elif t.is_dir():
            for p in sorted(t.rglob("*")):
                if p.is_file() and not (set(p.parts) & SKIP_DIRS):
                    yield p


def main(argv):
    targets = argv or [ROOT / "src"]
    allowed = load_allow()
    violations, allowed_hits, scanned = [], 0, 0
    for p in iter_files(targets):
        ext = p.suffix.lower()
        if ext not in CSTYLE | PYSTYLE | YAML:
            continue
        scanned += 1
        text = p.read_text(encoding="utf-8", errors="replace")
        name = rel(p)
        found = []

        def add(ln, rule, msg):
            found.append((ln, rule, msg))

        if ext in CSTYLE:
            comments, masked = scan_cstyle(text)
            check_comment_texts(comments, add)
            check_cstyle_structure(masked, add)
        elif ext in PYSTYLE:
            comments, masked = scan_py(text)
            check_comment_texts(comments, add)
            check_py_structure(masked, add)
        else:
            comments = [(i + 1, ln.split("#", 1)[1].strip()) for i, ln in enumerate(text.split("\n")) if "#" in ln]
            check_comment_texts([c for c in comments if SUPPRESS.search(c[1]) or PLACEHOLDER.search(c[1])], add)
        for ln, rule, msg in sorted(found):
            if (name, rule) in allowed:
                allowed_hits += 1
            else:
                violations.append(f"{name}:{ln} [{rule}] {msg}")
    for v in violations:
        print(v)
    print(f"-- đã quét {scanned} file | vi phạm: {len(violations)} | ngoại lệ MAIN cho phép: {allowed_hits}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
