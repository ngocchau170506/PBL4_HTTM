"""CNN_IOT — Tier-2 Server AI Verification Package (Option B1: Pretrained HD Verifier + ONNX Hook).

Handoff package for AI Team. Plugs directly into server_ai/server_core/deep_verifier.py
when TIER2_VERIFIER_MODE=b1 without modifying Pi Edge, ESP32 firmware, or Server REST routes.
"""
from .tier2_b1_verifier import PretrainedServerVerifier

__all__ = ["PretrainedServerVerifier"]
