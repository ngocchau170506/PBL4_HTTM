"""Gói chẩn đoán và ghi nhận sự cố ngoài luồng chính (fd_diagnose)."""
from fd_diagnose.auto_reporter import (
    AutoReporter,
    DiagnosisFinding,
    EvaluationReport,
)
from fd_diagnose.error_recorder import ErrorRecorder

__all__ = [
    "AutoReporter",
    "DiagnosisFinding",
    "ErrorRecorder",
    "EvaluationReport",
]
