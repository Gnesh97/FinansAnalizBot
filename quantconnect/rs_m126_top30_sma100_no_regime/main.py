"""LEAN port skeleton. Intentionally blocked until frozen contract has no blocking gaps."""

from pathlib import Path

try:  # QuantConnect cloud supplies this module; local py_compile does not.
    from AlgorithmImports import QCAlgorithm
except ImportError:  # pragma: no cover - local contract validation only
    class QCAlgorithm:  # type: ignore[no-redef]
        pass


ALGORITHM_VERSION = "static-contract-1"
STATIC_PARITY = "STATIC_PARITY"
PIT_VALIDATION = "PIT_VALIDATION"


class RelativeStrengthRotationLeanPort(QCAlgorithm):
    """Non-executable safety skeleton; no broker, short, or optimization paths."""

    def Initialize(self):
        mode = self.GetParameter("universe_mode") if hasattr(self, "GetParameter") else STATIC_PARITY
        mode = str(mode).upper()
        if mode not in {STATIC_PARITY, PIT_VALIDATION}:
            raise ValueError("universe_mode must be STATIC_PARITY or PIT_VALIDATION")
            
        contract_path = Path(__file__).with_name("static_parity_universe.json")
        if not contract_path.exists():
            raise RuntimeError("Frozen STATIC_PARITY universe artifact missing")
            
        if mode == STATIC_PARITY:
            raise RuntimeError(
                "STATIC_PARITY is non-executable due to unresolved price normalization and split/dividend provenance (STATIC_BLOCKING)."
            )
        elif mode == PIT_VALIDATION:
            raise RuntimeError(
                "PIT_VALIDATION is non-executable due to unresolved point-in-time universe rule (PIT_BLOCKING)."
            )
