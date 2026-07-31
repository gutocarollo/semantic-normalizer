"""Registry-bound, append-only per-occurrence reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .registry import ContractError, load_registry
from .schema_validation import load_package_schema, validate_instance

REASON_CODES = {
    "EXACT_APPROVED_FORM",
    "CONTEXT_DISAMBIGUATED",
    "INSUFFICIENT_CONTEXT",
    "NO_ALLOWED_CANDIDATE",
    "CONFLICTING_EVIDENCE",
}
REQUEST_SCHEMA = "reconciliation-request.schema.json"
RESPONSE_SCHEMA = "reconciliation-response.schema.json"
DECISION_SCHEMA = "reconciliation-decision.schema.json"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _payload_id(prefix: str, value: dict, excluded: str) -> str:
    payload = {key: item for key, item in value.items() if key != excluded}
    return f"{prefix}-{_hash(payload)}"


@dataclass(frozen=True)
class ReconciliationResult:
    state: str
    attempts: int
    candidate_id: str | None
    reason_code: str
    confidence: float
    evidence_span: dict
    transition_trace: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "attempts": self.attempts,
            "candidate_id": self.candidate_id,
            "reason_code": self.reason_code,
            "confidence": self.confidence,
            "evidence_span": self.evidence_span,
            "transition_trace": list(self.transition_trace),
        }


def _occurrence(
    context: str, start: int, end: int, language: str, registry: dict
) -> tuple[str, list[str]]:
    from .normalizer import normalize_text

    matches = []
    for record in normalize_text(context, "<reconciliation>", "text", registry):
        for item in record["ambiguous_candidates"]:
            if (
                item["start"] == start
                and item["end"] == end
                and item["alias"] == context[start:end]
            ):
                matches.append((record, item))
    if len(matches) != 1:
        raise ContractError(
            "span must identify exactly one registry-derived ambiguous occurrence"
        )
    record, match = matches[0]
    candidate_languages = {candidate["language"] for candidate in match["candidates"]}
    if (
        (record["language"] in {"en", "pt-BR"} and record["language"] != language)
        or language not in candidate_languages
    ):
        raise ContractError("request language differs from ambiguous occurrence")
    allowed = sorted({candidate["concept_id"] for candidate in match["candidates"]})
    occurrence_payload = {
        "input_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        "language": language,
        "span": {"start": start, "end": end, "text": context[start:end]},
    }
    return f"occ-{_hash(occurrence_payload)}", allowed


def validate_request(request: object, registry: dict | None = None) -> dict:
    registry = load_registry() if registry is None else registry
    schema = load_package_schema(REQUEST_SCHEMA)
    validate_instance(request, schema)
    if not isinstance(request, dict):
        raise ContractError("reconciliation request must be an object")
    context, span = request["context"], request["span"]
    start, end = span["start"], span["end"]
    if end <= start or end > len(context) or context[start:end] != span["text"]:
        raise ContractError("request span is outside or differs from context")
    input_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
    if request["input_sha256"] != input_hash:
        raise ContractError("request input SHA-256 differs from context")
    if (
        request["registry_version"] != registry["version"]
        or request["registry_sha256"] != registry["hash"]
        or request["registry_schema_sha256"] != registry["schema_hash"]
    ):
        raise ContractError("request registry binding differs from active workspace")
    occurrence_id, allowed = _occurrence(
        context, start, end, request["language"], registry
    )
    if request["occurrence_id"] != occurrence_id:
        raise ContractError("request occurrence_id differs from ambiguous occurrence")
    if request["allowed_candidates"] != allowed:
        raise ContractError("allowed_candidates differ from registry-derived occurrence")
    if request["query"] != span["text"] or request["limit"] != len(allowed):
        raise ContractError("request query or limit differs from occurrence")
    expected_id = _payload_id("req", request, "request_id")
    if request["request_id"] != expected_id:
        raise ContractError("request_id differs from deterministic request payload")
    return request


def apply_response(
    request: object, response: object, registry: dict | None = None
) -> ReconciliationResult:
    req = validate_request(request, registry)
    validate_instance(response, load_package_schema(RESPONSE_SCHEMA))
    if not isinstance(response, dict):
        raise ContractError("reconciliation response must be an object")
    candidate = response["candidate_id"]
    if candidate != "ABSTAIN" and candidate not in req["allowed_candidates"]:
        raise ContractError("candidate_id is outside allowed_candidates")
    reason = response["reason_code"]
    selection_reasons = {"EXACT_APPROVED_FORM", "CONTEXT_DISAMBIGUATED"}
    abstention_reasons = {
        "INSUFFICIENT_CONTEXT", "NO_ALLOWED_CANDIDATE", "CONFLICTING_EVIDENCE"
    }
    if candidate == "ABSTAIN" and reason not in abstention_reasons:
        raise ContractError("ABSTAIN requires an abstention reason_code")
    if candidate != "ABSTAIN" and reason not in selection_reasons:
        raise ContractError("selected candidate requires a selection reason_code")
    evidence = response["evidence_span"]
    start, end, text = evidence["start"], evidence["end"], evidence["text"]
    context = req["context"]
    if end <= start or end > len(context) or context[start:end] != text:
        raise ContractError("evidence_span is outside or differs from context")
    confidence = float(response["confidence"])
    if candidate == "ABSTAIN":
        if req["attempt"] == 1:
            state = "unresolved"
        elif reason == "NO_ALLOWED_CANDIDATE":
            state = "rejected"
        else:
            state = "review"
        selected = None
    else:
        if confidence <= 0:
            raise ContractError("accepted candidate requires positive confidence")
        state, selected = "accepted", candidate
    return ReconciliationResult(
        state=state,
        attempts=req["attempt"],
        candidate_id=selected,
        reason_code=reason,
        confidence=confidence,
        evidence_span=evidence,
        transition_trace=(
            "candidate", "unresolved", "constrained_resolution", "validation", state
        ),
    )


def make_request(
    context: str,
    start: int,
    end: int,
    language: str,
    allowed_candidates: list[str] | None = None,
    attempt: int = 1,
    registry: dict | None = None,
) -> dict:
    registry = load_registry() if registry is None else registry
    occurrence_id, derived = _occurrence(context, start, end, language, registry)
    if allowed_candidates is not None and sorted(set(allowed_candidates)) != derived:
        raise ContractError("caller candidates differ from registry-derived occurrence")
    request = {
        "query": context[start:end],
        "type": None,
        "type_strict": "any",
        "properties": [],
        "limit": len(derived),
        "language": language,
        "span": {"start": start, "end": end, "text": context[start:end]},
        "context": context,
        "allowed_candidates": derived,
        "attempt": attempt,
        "occurrence_id": occurrence_id,
        "input_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        "registry_version": registry["version"],
        "registry_sha256": registry["hash"],
        "registry_schema_sha256": registry["schema_hash"],
        "request_id": "",
    }
    request["request_id"] = _payload_id("req", request, "request_id")
    return validate_request(request, registry)


def load_workspace(directory: str | Path) -> tuple[Path, dict]:
    workspace = Path(directory)
    required = {
        "registry.jsonl", "registry.schema.json", "candidates.jsonl", "decisions.jsonl"
    }
    if not workspace.is_dir() or any(not (workspace / name).is_file() for name in required):
        raise ContractError("reconciliation requires an initialized external workspace")
    return workspace, load_registry(
        workspace / "registry.jsonl", workspace / "registry.schema.json"
    )


def _read_ledger(path: Path, schema_name: str) -> list[dict]:
    schema = load_package_schema(schema_name)
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ContractError(f"{path.name} line {line_no}: blank JSONL record")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"{path.name} line {line_no}: invalid JSON") from exc
        validate_instance(record, schema, path=f"{path.name}[{line_no}]")
        records.append(record)
    return records


@contextmanager
def _workspace_lock(workspace: Path):
    lock_path = workspace / ".reconciliation.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _append(path: Path, record: dict) -> None:
    data = (_canonical(record) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                raise OSError(f"append to {path} did not make progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validated_ledgers(
    workspace: Path, registry: dict
) -> tuple[list[dict], list[dict]]:
    requests = _read_ledger(workspace / "candidates.jsonl", REQUEST_SCHEMA)
    request_ids: set[str] = set()
    occurrence_attempts: set[tuple[str, int]] = set()
    request_by_id = {}
    for request in requests:
        validate_request(request, registry)
        if request["request_id"] in request_ids:
            raise ContractError("candidate ledger contains duplicate request_id")
        key = (request["occurrence_id"], request["attempt"])
        if key in occurrence_attempts:
            raise ContractError("candidate ledger contains duplicate occurrence attempt")
        request_ids.add(request["request_id"])
        occurrence_attempts.add(key)
        request_by_id[request["request_id"]] = request

    decisions = _read_ledger(workspace / "decisions.jsonl", DECISION_SCHEMA)
    decision_ids: set[str] = set()
    decided_requests: set[str] = set()
    for decision in decisions:
        request = request_by_id.get(decision["request_id"])
        if request is None:
            raise ContractError("decision references a request absent from candidate ledger")
        if decision["decision_id"] != _payload_id("dec", decision, "decision_id"):
            raise ContractError("decision_id differs from deterministic decision payload")
        if decision["decision_id"] in decision_ids:
            raise ContractError("decision ledger contains duplicate decision_id")
        if decision["request_id"] in decided_requests:
            raise ContractError("decision ledger contains duplicate request decision")
        bindings = (
            ("occurrence_id", "occurrence_id"),
            ("registry_version", "registry_version"),
            ("registry_sha256", "registry_sha256"),
            ("registry_schema_sha256", "registry_schema_sha256"),
            ("input_sha256", "input_sha256"),
            ("attempt", "attempt"),
        )
        if any(decision[left] != request[right] for left, right in bindings):
            raise ContractError("decision bindings differ from candidate request")
        if decision["request_sha256"] != _hash(request):
            raise ContractError("decision request_sha256 differs from candidate request")
        if decision["result"]["attempts"] != request["attempt"]:
            raise ContractError("decision result attempts differ from candidate request")
        result = decision["result"]
        expected_trace = [
            "candidate", "unresolved", "constrained_resolution",
            "validation", result["state"],
        ]
        if result["transition_trace"] != expected_trace:
            raise ContractError("decision transition trace is invalid")
        if result["state"] == "accepted":
            if result["candidate_id"] not in request["allowed_candidates"]:
                raise ContractError("accepted decision candidate is outside occurrence")
            if (
                result["reason_code"] not in {"EXACT_APPROVED_FORM", "CONTEXT_DISAMBIGUATED"}
                or result["confidence"] <= 0
            ):
                raise ContractError("accepted decision evidence is inconsistent")
        elif result["candidate_id"] is not None:
            raise ContractError("non-accepted decision cannot retain a candidate")
        elif result["reason_code"] not in {
            "INSUFFICIENT_CONTEXT", "NO_ALLOWED_CANDIDATE", "CONFLICTING_EVIDENCE"
        }:
            raise ContractError("abstained decision reason is inconsistent")
        if (
            (result["state"] == "rejected") !=
            (request["attempt"] == 2 and result["reason_code"] == "NO_ALLOWED_CANDIDATE")
        ):
            raise ContractError("rejected decision reason is inconsistent")
        allowed_states = (
            {"accepted", "unresolved"}
            if request["attempt"] == 1
            else {"accepted", "review", "rejected"}
        )
        if result["state"] not in allowed_states:
            raise ContractError("decision state is invalid for request attempt")
        decision_ids.add(decision["decision_id"])
        decided_requests.add(decision["request_id"])
    decision_by_request = {item["request_id"]: item for item in decisions}
    requests_by_occurrence: dict[str, list[dict]] = {}
    for request in requests:
        requests_by_occurrence.setdefault(request["occurrence_id"], []).append(request)
    for occurrence_requests in requests_by_occurrence.values():
        ordered = sorted(occurrence_requests, key=lambda item: item["attempt"])
        if [item["attempt"] for item in ordered] not in ([1], [1, 2]):
            raise ContractError("candidate ledger has a non-sequential attempt history")
        if len(ordered) == 2:
            prior = decision_by_request.get(ordered[0]["request_id"])
            if prior is None or prior["result"]["state"] != "unresolved":
                raise ContractError("attempt 2 lacks an unresolved attempt 1 decision")
    return requests, decisions


def create_workspace_request(
    directory: str | Path,
    context: str,
    start: int,
    end: int,
    language: str,
) -> dict:
    workspace, registry = load_workspace(directory)
    with _workspace_lock(workspace):
        previous, decisions = _validated_ledgers(workspace, registry)
        occurrence_id, _ = _occurrence(context, start, end, language, registry)
        occurrence_requests = [
            item for item in previous if item["occurrence_id"] == occurrence_id
        ]
        occurrence_requests.sort(key=lambda item: item["attempt"])
        if not occurrence_requests:
            attempt = 1
        elif len(occurrence_requests) == 1 and occurrence_requests[0]["attempt"] == 1:
            prior = [
                item for item in decisions
                if item["request_id"] == occurrence_requests[0]["request_id"]
            ]
            if not prior:
                raise ContractError("attempt 1 is pending and cannot be retried")
            if prior[0]["result"]["state"] != "unresolved":
                raise ContractError("terminal reconciliation result cannot be retried")
            attempt = 2
        else:
            raise ContractError("ambiguous occurrence has exhausted its two attempts")
        request = make_request(
            context, start, end, language, attempt=attempt, registry=registry
        )
        if any(item["request_id"] == request["request_id"] for item in previous):
            raise ContractError("duplicate reconciliation request")
        _append(workspace / "candidates.jsonl", request)
        return request


def apply_workspace_response(
    directory: str | Path,
    request: object,
    response: object,
    reviewer: str,
    rationale: str,
    protected_slot_comparison: str,
    timestamp: str | None = None,
) -> dict:
    workspace, registry = load_workspace(directory)
    if not reviewer.strip() or not rationale.strip():
        raise ContractError("reviewer and rationale are required")
    with _workspace_lock(workspace):
        requests, decisions = _validated_ledgers(workspace, registry)
        validate_request(request, registry)
        if not isinstance(request, dict):
            raise ContractError("request must be an object")
        matching = [
            item for item in requests if item["request_id"] == request["request_id"]
        ]
        if len(matching) != 1 or matching[0] != request:
            raise ContractError("request is forged or absent from candidates ledger")
        if any(item["request_id"] == request["request_id"] for item in decisions):
            raise ContractError("reconciliation request was already applied")
        result = apply_response(request, response, registry).as_dict()
        decided_at = timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        decision = {
            "decision_version": "1.0.0",
            "decision_id": "",
            "request_id": request["request_id"],
            "occurrence_id": request["occurrence_id"],
            "registry_version": request["registry_version"],
            "registry_sha256": request["registry_sha256"],
            "registry_schema_sha256": request["registry_schema_sha256"],
            "input_sha256": request["input_sha256"],
            "request_sha256": _hash(request),
            "response_sha256": _hash(response),
            "reviewer": reviewer,
            "timestamp": decided_at,
            "rationale": rationale,
            "protected_slot_comparison": protected_slot_comparison,
            "attempt": request["attempt"],
            "result": result,
        }
        decision["decision_id"] = _payload_id("dec", decision, "decision_id")
        validate_instance(decision, load_package_schema(DECISION_SCHEMA))
        _append(workspace / "decisions.jsonl", decision)
        return decision
