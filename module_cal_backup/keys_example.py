"""
Example security-access key hooks.

Memory read on most modules is gated by SecurityAccess (0x27): the module
sends a seed, you return a key derived from it by an OEM-specific algorithm.
Those algorithms are proprietary and are NOT shipped here. Supply your own
`key_fn(level, seed) -> key` for the vehicle you are authorized to service.

The two examples below are the only two publicly-known trivial cases; real
modules will reject them and the backup falls back to identification DIDs.
"""
from __future__ import annotations


def key_fn_passthrough(level: int, seed: bytes) -> bytes:
    """Some bench/dev ECUs accept the seed echoed back as the key."""
    return seed


def key_fn_xor(secret: int):
    """Factory for the classic 2-byte XOR scheme seen on older KWP modules."""
    def _fn(level: int, seed: bytes) -> bytes:
        val = int.from_bytes(seed, "big") ^ secret
        return val.to_bytes(len(seed), "big")
    return _fn
