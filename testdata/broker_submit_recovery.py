from __future__ import annotations

import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


INTENT_STATES = {
    "PREPARED",
    "POST_IN_PROGRESS",
    "BROKER_CONFIRMED",
    "LEDGER_COMMITTED",
    "AMBIGUOUS_RESULT",
    "BLOCKED",
}
_CLIENT_ID = re.compile(r"^fpap-[0-9a-f]{40}$")


class SubmitIntentError(RuntimeError):
    """Fail-closed local broker-submit intent journal error."""


def intent_journal_path(latest_dir: str | Path) -> Path:
    latest = Path(latest_dir)
    current = latest.resolve()
    for _ in range(4):
        if current.name == "reports":
            return current.parent / "state" / "broker_submit_intents.json"
        current = current.parent
    return latest / "state" / "broker_submit_intents.json"


def _finite(value, field: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SubmitIntentError(f"invalid intent journal: {field} is not numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise SubmitIntentError(f"invalid intent journal: {field} must be finite and non-negative")
    return str(number)


def _text(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SubmitIntentError(f"invalid intent journal: {field} is empty")
    return text


def _validate_intent(intent: object) -> dict[str, object]:
    if not isinstance(intent, dict):
        raise SubmitIntentError("invalid intent journal: intent is not an object")
    required = {"client_order_id", "symbol", "side", "endpoint", "created_at", "recovery_state", "evidence"}
    if set(intent) != required | {"qty"} and set(intent) != required | {"notional"}:
        raise SubmitIntentError("invalid intent journal: intent schema mismatch")
    client_order_id = _text(intent.get("client_order_id"), "client_order_id")
    if not _CLIENT_ID.fullmatch(client_order_id):
        raise SubmitIntentError("invalid intent journal: client_order_id is not deterministic")
    symbol = _text(intent.get("symbol"), "symbol").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
        raise SubmitIntentError("invalid intent journal: symbol is invalid")
    side = _text(intent.get("side"), "side").lower()
    if side != "buy":
        raise SubmitIntentError("invalid intent journal: side must be buy")
    endpoint = _text(intent.get("endpoint"), "endpoint")
    if not endpoint.startswith("https://paper-api.alpaca.markets/"):
        raise SubmitIntentError("invalid intent journal: endpoint is not paper")
    created_at = _text(intent.get("created_at"), "created_at")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SubmitIntentError("invalid intent journal: created_at is invalid") from exc
    state = _text(intent.get("recovery_state"), "recovery_state").upper()
    if state not in INTENT_STATES:
        raise SubmitIntentError("invalid intent journal: recovery_state is invalid")
    evidence = intent.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        raise SubmitIntentError("invalid intent journal: evidence is invalid")
    amount_key = "qty" if "qty" in intent else "notional"
    result = {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "side": side,
        "endpoint": endpoint,
        "created_at": created_at,
        "recovery_state": state,
        "evidence": evidence,
        amount_key: _finite(intent.get(amount_key), amount_key),
    }
    return result


def _validate_document(document: object) -> dict[str, dict[str, object]]:
    if not isinstance(document, dict) or set(document) != {"schema_version", "intents"}:
        raise SubmitIntentError("invalid intent journal: document schema mismatch")
    if document.get("schema_version") != 1 or not isinstance(document.get("intents"), dict):
        raise SubmitIntentError("invalid intent journal: document version or intents invalid")
    intents: dict[str, dict[str, object]] = {}
    for client_order_id, intent in document["intents"].items():
        validated = _validate_intent(intent)
        if client_order_id != validated["client_order_id"]:
            raise SubmitIntentError("invalid intent journal: client_order_id key mismatch")
        intents[client_order_id] = validated
    return intents


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SubmitIntentError(f"intent journal lock held: {lock_path.name}; fail closed") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _read(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        return _validate_document(json.loads(path.read_text(encoding="utf-8")))
    except SubmitIntentError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitIntentError("invalid intent journal: cannot read or parse") from exc


def _write(path: Path, intents: dict[str, dict[str, object]]) -> None:
    document = {"schema_version": 1, "intents": intents}
    _validate_document(document)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        _read(temp_path)
        os.replace(temp_path, path)
    except SubmitIntentError:
        raise
    except OSError as exc:
        raise SubmitIntentError("intent journal atomic write failed") from exc
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


class SubmitIntentStore:
    def __init__(self, latest_dir: str | Path):
        self.path = intent_journal_path(latest_dir)

    def get(self, client_order_id: str) -> dict[str, object] | None:
        lock_path = self.path.with_name(self.path.name + ".lock")
        if lock_path.exists():
            raise SubmitIntentError(f"intent journal lock held: {lock_path.name}; fail closed")
        return _read(self.path).get(client_order_id)

    def prepare(self, values: dict[str, object]) -> dict[str, object]:
        client_order_id = _text(values.get("client_order_id"), "client_order_id")
        amount_key = "qty" if values.get("qty") not in (None, "") else "notional"
        intent = _validate_intent({
            "client_order_id": client_order_id,
            "symbol": values.get("symbol"),
            "side": values.get("side"),
            amount_key: values.get(amount_key),
            "endpoint": values.get("endpoint"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "recovery_state": "PREPARED",
            "evidence": [],
        })
        with _lock(self.path):
            intents = _read(self.path)
            existing = intents.get(client_order_id)
            if existing is not None:
                return existing
            intents[client_order_id] = intent
            _write(self.path, intents)
            return intent

    def transition(self, client_order_id: str, state: str, *, evidence: dict[str, object] | None = None) -> dict[str, object]:
        with _lock(self.path):
            intents = _read(self.path)
            intent = intents.get(client_order_id)
            if intent is None:
                raise SubmitIntentError("intent journal: missing prepared intent")
            state = _text(state, "recovery_state").upper()
            if state not in INTENT_STATES:
                raise SubmitIntentError("intent journal: invalid transition state")
            intent["recovery_state"] = state
            if evidence is not None:
                intent["evidence"] = list(intent["evidence"]) + [{str(k): str(v)[:200] for k, v in evidence.items()}]
            intent = _validate_intent(intent)
            intents[client_order_id] = intent
            _write(self.path, intents)
            return intent
