"""Fail-Closed Gate Runner & Evidence Evaluator for PI_AI.

Eliminates F-001 (AutoReporter False PASS on missing metrics/fault reports),
F-002 (FaultInjector PASS despite recovered_after_fault=False),
F-003 (health/verdict mismatch), F-004 (missing runner), and F-010 (0-test discovery PASS).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

Verdict = Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN", "PARTIAL", "INVALIDATED"]
Health = Literal["OK", "DEGRADED", "DOWN"]

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
MANIFEST_PATH = Path(__file__).resolve().parent / "gate_manifest.json"
REPORTS_DIR = ROOT_DIR / "gate_reports"

# Strict forbidden patterns in pi_edge code/scripts (LCK-01 / network safety)
FORBIDDEN_PI_PATTERNS = [
    (re.compile(r"\bnmcli\s+dev\s+set\b"), "Forbidden NetworkManager mutation (nmcli dev set)"),
    (re.compile(r"\bsudo\s+-n\s+ip\s+(addr|route|link)\b"), "Forbidden hidden sudo network mutation"),
    (re.compile(r"if\s+[\"']ACK[\"']\s+in\s+line"), "Forbidden F-NACK substring bug ('ACK' in line matches NACK)"),
    (re.compile(r"OPEN_LAB_NO_TOKENS"), "Forbidden open-lab default admin fallback in production"),
]


@dataclass(frozen=True)
class FaultCheckInput:
    scenario_id: str
    requires_recovery: bool
    recovered_after_fault: bool
    recovery_time_ms: Optional[float]
    recovery_deadline_ms: Optional[float]
    matched_expected_codes: bool
    observed_on_real_sut: bool


@dataclass(frozen=True)
class EvaluationSummary:
    overall_verdict: Verdict
    health: Health
    environment_label: str
    source_hash: str
    reasons: Tuple[str, ...]
    passed_checks: int
    failed_checks: int
    blocked_checks: int


def compute_source_tree_hash(root: Path = ROOT_DIR) -> str:
    """Compute deterministic SHA-256 hash over ALL source/config files (includes pi_edge/evidence/*.py!)."""
    h = hashlib.sha256()
    skip_any_parts = {".git", "__pycache__", ".venv", "venv", ".pytest_cache", ".vault", ".theia"}
    skip_rel_prefixes = (
        "gate_reports/",
        "server_ai/data/",
        "server_ai/evidence/",
        "server_ai/bin/",
        "pi_edge/violations/",
        "pi_edge/logs/",
        "pi_edge/models/",
        "pi_edge/calibration/",
    )
    skip_exact_names = {"wifi_config.h", "voice_clips.h", "pi.env", "tokens.json", "CLAUDE.md"}
    allowed_suffixes = {
        ".py", ".json", ".md", ".ino", ".sh", ".ps1", ".service", ".rules",
        ".html", ".js", ".mjs", ".css", ".txt", ".example", ".h",
    }
    files: List[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in skip_any_parts for part in p.parts):
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel.startswith(pref) for pref in skip_rel_prefixes):
            continue
        if p.name in skip_exact_names:
            continue
        if p.suffix in allowed_suffixes:
            files.append(p)
    for f in files:
        rel = f.relative_to(root).as_posix().encode("utf-8")
        h.update(rel + b"\x00")
        h.update(f.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def evaluate_fault_report(fault: FaultCheckInput) -> Tuple[Verdict, str]:
    """Fail-closed fault evaluation (fixes F-002)."""
    if not fault.matched_expected_codes:
        return "FAIL", f"{fault.scenario_id}: expected fault codes not matched"
    if fault.requires_recovery and not fault.recovered_after_fault:
        return "FAIL", f"{fault.scenario_id}: recovery required but recovered_after_fault=False"
    if (
        fault.requires_recovery
        and fault.recovery_deadline_ms is not None
        and (fault.recovery_time_ms is None or fault.recovery_time_ms > fault.recovery_deadline_ms)
    ):
        return "FAIL", f"{fault.scenario_id}: recovery exceeded deadline ({fault.recovery_time_ms}ms > {fault.recovery_deadline_ms}ms)"
    if not fault.observed_on_real_sut:
        return "BLOCKED", f"{fault.scenario_id}: synthetic-only observation, real SUT required"
    return "PASS", f"{fault.scenario_id}: verified recovery"


def evaluate_evidence_fail_closed(
    metrics_summary: Optional[Dict[str, Any]],
    fault_reports: Sequence[FaultCheckInput],
    test_counts: Dict[str, int],
    source_hash: str,
    expected_source_hash: Optional[str] = None,
    environment_label: str = "PC_PREFLIGHT",
    require_fault_reports: bool = True,
) -> EvaluationSummary:
    """Fail-closed evidence aggregator (fixes F-001, F-002, F-003, F-010)."""
    reasons: List[str] = []
    passed = 0
    failed = 0
    blocked = 0

    if expected_source_hash and source_hash != expected_source_hash:
        return EvaluationSummary(
            overall_verdict="INVALIDATED",
            health="DEGRADED",
            environment_label=environment_label,
            source_hash=source_hash,
            reasons=(f"Source hash changed ({expected_source_hash} -> {source_hash}); prior PASS invalidated",),
            passed_checks=0,
            failed_checks=0,
            blocked_checks=1,
        )

    # 1. Test counts (0 tests = BLOCKED/FAIL, never PASS)
    total_tests = int(test_counts.get("total", 0))
    failed_tests = int(test_counts.get("failed", 0))
    if total_tests <= 0:
        blocked += 1
        reasons.append("Test count is 0 (F-010 fail-closed: 0 tests cannot PASS)")
    elif failed_tests > 0:
        failed += 1
        reasons.append(f"{failed_tests}/{total_tests} tests failed")
    else:
        passed += 1

    # 2. Metrics presence & thresholds (None metrics = BLOCKED, never PASS -> fixes F-001)
    if not metrics_summary or not isinstance(metrics_summary, dict):
        blocked += 1
        reasons.append("Missing metrics_summary (F-001 fail-closed)")
    elif "passed_thresholds" not in metrics_summary:
        blocked += 1
        reasons.append("metrics_summary missing explicit passed_thresholds boolean")
    elif not bool(metrics_summary["passed_thresholds"]):
        failed += 1
        reasons.append("metrics_summary.passed_thresholds is False")
    elif int(metrics_summary.get("total_packets", 0)) <= 0:
        blocked += 1
        reasons.append("metrics_summary.total_packets is 0 (no observed traffic)")
    else:
        passed += 1

    # 3. Fault reports presence & recovery check (fixes F-001 & F-002)
    if require_fault_reports and len(fault_reports) == 0:
        blocked += 1
        reasons.append("No fault_reports provided (F-001 fail-closed)")
    else:
        for fr in fault_reports:
            v, msg = evaluate_fault_report(fr)
            if v == "PASS":
                passed += 1
            elif v == "FAIL":
                failed += 1
                reasons.append(msg)
            else:
                blocked += 1
                reasons.append(msg)

    # Health ALWAYS derived from verdict (fixes F-003)
    if failed > 0:
        verdict: Verdict = "FAIL"
        health: Health = "DOWN"
    elif blocked > 0:
        verdict = "BLOCKED"
        health = "DEGRADED"
    else:
        verdict = "PASS"
        health = "OK"

    return EvaluationSummary(
        overall_verdict=verdict,
        health=health,
        environment_label=environment_label,
        source_hash=source_hash,
        reasons=tuple(reasons),
        passed_checks=passed,
        failed_checks=failed,
        blocked_checks=blocked,
    )


def _run_policy_scan(targets: List[str]) -> Tuple[Verdict, str, int]:
    violations: List[str] = []
    scanned = 0
    for rel in targets:
        base = ROOT_DIR / rel
        if not base.exists():
            return "BLOCKED", f"Target path missing: {rel}", 0
        for p in sorted(base.rglob("*")):
            if "__pycache__" in p.parts or "tests" in p.parts or not p.is_file():
                continue
            if p.suffix not in {".py", ".sh", ".ps1", ".service"}:
                continue
            scanned += 1
            text = p.read_text(encoding="utf-8", errors="ignore")
            for rx, desc in FORBIDDEN_PI_PATTERNS:
                if rx.search(text):
                    violations.append(f"{p.relative_to(ROOT_DIR).as_posix()}: {desc}")
    if scanned == 0:
        return "BLOCKED", "Scanned 0 files", 0
    if violations:
        return "FAIL", "; ".join(violations), scanned
    return "PASS", f"Scanned {scanned} files, 0 policy violations", scanned


def _parse_unittest_count(output: str) -> int:
    ran_matches = re.findall(r"Ran\s+(\d+)\s+test", output)
    ran = int(ran_matches[-1]) if ran_matches else 0
    skip_matches = re.findall(r"skipped=(\d+)", output)
    skipped = int(skip_matches[-1]) if skip_matches else 0
    return max(0, ran - skipped)


def run_manifest_stage(stage: str = "pc-preflight") -> Dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = manifest["stages"].get(stage)
    if not entries:
        raise ValueError(f"Unknown stage: {stage}")

    source_hash = compute_source_tree_hash()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    results: List[Dict[str, Any]] = []
    any_fail = False
    any_blocked = False

    for item in entries:
        gid = item["gate_id"]
        kind = item["kind"]
        t0 = time.monotonic()

        if kind == "manual_hardware":
            hw_ev_path = REPORTS_DIR / "hardware_bench_evidence.json"
            matched_ev = None
            if hw_ev_path.exists():
                try:
                    hw_doc = json.loads(hw_ev_path.read_text(encoding="utf-8"))
                    if hw_doc.get("source_hash") == source_hash:
                        matched_ev = (hw_doc.get("gates") or {}).get(gid)
                except Exception:
                    matched_ev = None
            if isinstance(matched_ev, dict) and matched_ev.get("status") == "PASS" and matched_ev.get("observed_on_real_hw"):
                results.append({
                    "gate_id": gid,
                    "title": item["title"],
                    "status": "PASS",
                    "device": item.get("device"),
                    "detail": str(matched_ev.get("detail") or "Verified on real physical hardware"),
                    "duration_s": 0.0,
                })
            else:
                any_blocked = True
                reason = (
                    "Hardware evidence invalidated (source_hash changed since bench run)."
                    if hw_ev_path.exists()
                    else "Requires real physical hardware bench (never mocked)."
                )
                results.append({
                    "gate_id": gid,
                    "title": item["title"],
                    "status": "NOT_RUN",
                    "device": item.get("device"),
                    "detail": reason,
                    "duration_s": 0.0,
                })
            continue

        if kind == "connectivity_guard":
            from contracts.stage_guard import verify_cross_layer_connectivity

            conn = verify_cross_layer_connectivity()
            verdict = "PASS" if conn["ok"] else "FAIL"
            if verdict == "FAIL":
                any_fail = True
            failed_links = [f"{x['link_id']}: {x['detail']}" for x in conn["links"] if not x["ok"]]
            results.append({
                "gate_id": gid,
                "title": item["title"],
                "status": verdict,
                "links_passed": conn["passed"],
                "links_total": conn["total"],
                "detail": (
                    f"{conn['passed']}/{conn['total']} cross-layer links verified"
                    if conn["ok"]
                    else "; ".join(failed_links)
                ),
                "duration_s": round(time.monotonic() - t0, 3),
            })
            continue

        if kind == "policy_scan":
            verdict, detail, scanned = _run_policy_scan(item["targets"])
            any_fail = any_fail or (verdict == "FAIL")
            any_blocked = any_blocked or (verdict == "BLOCKED")
            results.append({
                "gate_id": gid,
                "title": item["title"],
                "status": verdict,
                "files_scanned": scanned,
                "detail": detail,
                "duration_s": round(time.monotonic() - t0, 3),
            })
            continue

        if kind == "command":
            cmd = [sys.executable if tok == "{python}" else tok for tok in item["command"]]
            proc = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, capture_output=True, text=True, timeout=120)
            out = (proc.stdout + "\n" + proc.stderr).strip()
            verdict = "PASS" if proc.returncode == 0 else "FAIL"
            if verdict == "FAIL":
                any_fail = True
            results.append({
                "gate_id": gid,
                "title": item["title"],
                "status": verdict,
                "exit_code": proc.returncode,
                "command": " ".join(cmd),
                "detail": out[-1000:] if out else "OK",
                "duration_s": round(time.monotonic() - t0, 3),
            })
            continue

        if kind == "unittest":
            cmd = [sys.executable, "-m", "unittest", item["module"], "-v"]
            proc = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, capture_output=True, text=True, timeout=120)
            out = (proc.stdout + "\n" + proc.stderr).strip()
            count = _parse_unittest_count(out)
            min_tests = int(item.get("min_tests", 1))
            if count < min_tests:
                verdict = "BLOCKED" if proc.returncode == 0 else "FAIL"
                detail = f"Executed {count} non-skipped tests < required {min_tests}. Output: {out[-800:]}"
            elif proc.returncode != 0:
                verdict = "FAIL"
                detail = out[-1200:]
            else:
                verdict = "PASS"
                detail = f"{count} tests passed"
            any_fail = any_fail or (verdict == "FAIL")
            any_blocked = any_blocked or (verdict == "BLOCKED")
            results.append({
                "gate_id": gid,
                "title": item["title"],
                "status": verdict,
                "tests_run": count,
                "min_tests": min_tests,
                "exit_code": proc.returncode,
                "command": " ".join(cmd),
                "detail": detail,
                "duration_s": round(time.monotonic() - t0, 3),
            })
            continue

        # Fail-closed on unknown gate kind (fixes BUG-18!)
        any_fail = True
        results.append({
            "gate_id": gid,
            "title": item.get("title", gid),
            "status": "FAIL",
            "detail": f"Unknown gate kind: {kind}",
            "duration_s": round(time.monotonic() - t0, 3),
        })

    if any_fail:
        overall: Verdict = "FAIL"
        health: Health = "DOWN"
    elif any_blocked:
        overall = "BLOCKED"
        health = "DEGRADED"
    else:
        overall = "PASS"
        health = "OK"

    env_label = "PC_PREFLIGHT" if stage == "pc-preflight" else "REAL_HARDWARE_VERIFIED"
    report = {
        "stage": stage,
        "environment_label": env_label,
        "source_hash": source_hash,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_verdict": overall,
        "health": health,
        "results": results,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = REPORTS_DIR / f"gate_{stage}_{source_hash}.json"
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["report_path"] = str(out_file)
    if stage == "pc-preflight":
        try:
            from contracts.stage_guard import record_stage_a_lock, verify_cross_layer_connectivity
            record_stage_a_lock(source_hash, overall, verify_cross_layer_connectivity())
        except Exception:
            pass
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="PI_AI Fail-Closed Gate Runner")
    ap.add_argument("--stage", default="pc-preflight", choices=["pc-preflight", "hardware-bench"])
    ap.add_argument("--strict", action="store_true", help="Exit non-zero unless overall_verdict == PASS")
    args = ap.parse_args()
    report = run_manifest_stage(args.stage)
    print(f"=== PI_AI GATE RUNNER [{report['stage']} | {report['environment_label']}] ===")
    print(f"Source Hash : {report['source_hash']}")
    print(f"Verdict     : {report['overall_verdict']} (health={report['health']})")
    for r in report["results"]:
        extra = f" ({r['tests_run']} tests)" if "tests_run" in r else ""
        print(f"  [{r['status']:7s}] {r['gate_id']}{extra} — {r['title']}")
        if r["status"] != "PASS":
            print(f"            detail: {r['detail'][:240]}")
    print(f"Artifact    : {report['report_path']}")
    if args.strict and report["overall_verdict"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
