"""Gói cổng cắm AI Adapter (fd_process.ai)."""
from fd_process.ai.adapter_base import run_ai
from fd_process.ai.stub_adapter import StubAdapter

__all__ = ["StubAdapter", "run_ai"]
