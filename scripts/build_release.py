#!/usr/bin/env python3
"""Build and validate deterministic offline wheel and skill ZIP artifacts."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]
DIST = PROJECT / "dist"
REPORTS = PROJECT / "reports"
VERSION = "0.4.0"
DISTRIBUTION = "semantic_normalizer"
WHEEL_NAME = f"{DISTRIBUTION}-{VERSION}-py3-none-any.whl"
ZIP_NAME = f"semantic-normalizer-skill-{VERSION}.zip"
ZIP_PREFIX = f"semantic-normalizer-{VERSION}"
DIST_INFO = f"{DISTRIBUTION}-{VERSION}.dist-info"
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
EXPECTED_WHEEL_VERSION = "0.47.0"
PACKAGE_REPORT = REPORTS / "package-validation.json"
RELEASE_MANIFEST = REPORTS / "release-manifest.json"
CHECKSUMS = PROJECT / "checksums.sha256"
EXCLUDED_REPORT_JSONL = {
    "dev-auto-match-adjudication-blind.jsonl",
    "dev-auto-match-adjudication-seed-final.jsonl",
    "dev-auto-match-adjudication-seed.jsonl",
    "dev-auto-match-blind-pending-final.jsonl",
    "dev-auto-match-blind-pending.jsonl",
    "dev-auto-match-candidates-final.jsonl",
    "dev-auto-match-candidates.jsonl",
}
# 0.3.0 dropped the held-out custody modules; the wheel now has to carry the runtime the
# skill actually ships, plus the registry and its governance records.
PUBLIC_CONTRACT_SCHEMAS = (
    "registry.schema.json",
    "sidecar.schema.json",
    "reconciliation-request.schema.json",
    "reconciliation-response.schema.json",
    "reconciliation-decision.schema.json",
)
PUBLIC_EVALUATION_CONFIGS = (
    "registry.jsonl",
    "registry.release.json",
    "registry.provenance.jsonl",
)
REQUIRED_WHEEL_MEMBERS = {
    "semantic_normalizer/__init__.py",
    "semantic_normalizer/__main__.py",
    "semantic_normalizer/cli.py",
    "semantic_normalizer/evaluator.py",
    "semantic_normalizer/exporters.py",
    "semantic_normalizer/normalizer.py",
    "semantic_normalizer/reconciliation.py",
    "semantic_normalizer/registry.py",
    "semantic_normalizer/schema_validation.py",
    *(
        f"semantic_normalizer/data/{name}"
        for name in PUBLIC_CONTRACT_SCHEMAS
    ),
    *(
        f"semantic_normalizer/data/{name}"
        for name in PUBLIC_EVALUATION_CONFIGS
    ),
}
FORBIDDEN_WHEEL_PARTS = {"tests", "test", "fixtures", "reports", "heldout"}
FORBIDDEN_WHEEL_SUFFIXES = {".age", ".ciphertext"}


class BuildError(RuntimeError):
    """Release build or validation failed."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return digest(path.read_bytes())


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        rendered = " ".join(command)
        raise BuildError(
            f"command failed ({result.returncode}): {rendered}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def assert_environment() -> dict:
    if sys.version_info[:2] != (3, 14):
        raise BuildError(f"Python 3.14 required, got {sys.version.split()[0]}")
    wheel_result = run([sys.executable, "-m", "wheel", "version"])
    wheel_version = wheel_result.stdout.strip().removeprefix("wheel ")
    if wheel_version != EXPECTED_WHEEL_VERSION:
        raise BuildError(
            f"wheel {EXPECTED_WHEEL_VERSION} required, got {wheel_version}"
        )
    return {
        "python": sys.version.split()[0],
        "wheel": wheel_version,
        "pip": run([sys.executable, "-m", "pip", "--version"]).stdout.split()[1],
        "network_used": False,
        "build_backend_used": False,
    }


def package_data_files() -> list[Path]:
    source = PROJECT / "src" / "semantic_normalizer"
    config = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        patterns = config["tool"]["semantic-normalizer"]["package-data"]["include"]
    except (KeyError, TypeError) as exc:
        raise BuildError("pyproject package-data include contract is missing") from exc
    if (
        not isinstance(patterns, list)
        or not patterns
        or any(
            not isinstance(pattern, str)
            or not pattern.startswith("data/")
            or ".." in PurePosixPath(pattern).parts
            for pattern in patterns
        )
    ):
        raise BuildError("pyproject package-data include contract is invalid")
    selected: set[Path] = set()
    for pattern in patterns:
        for path in source.glob(pattern):
            if path.is_symlink():
                raise BuildError(f"symbolic link rejected from package data: {path}")
            if path.is_file():
                selected.add(path)
    expected = {
        path
        for path in (source / "data").rglob("*")
        if path.is_file()
        and path.suffix != ".py"
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }
    if selected != expected:
        missing = sorted(str(path.relative_to(source)) for path in expected - selected)
        extra = sorted(str(path.relative_to(source)) for path in selected - expected)
        raise BuildError(
            f"pyproject package-data contract mismatch: missing={missing}, extra={extra}"
        )
    mutable_ledgers = {"candidates.jsonl", "decisions.jsonl"}
    if any(path.name in mutable_ledgers for path in selected):
        raise BuildError("mutable reconciliation ledger cannot be package data")
    return sorted(selected)


def registry_version() -> str:
    """Version the shipped registry declares, read from its release record."""
    release = PROJECT / "src" / "semantic_normalizer" / "data" / "registry.release.json"
    return json.loads(release.read_text(encoding="utf-8"))["version"]


def governed_data_names() -> list[str]:
    data = PROJECT / "src" / "semantic_normalizer" / "data"
    return [path.relative_to(data).as_posix() for path in package_data_files()]


def package_files() -> list[tuple[Path, PurePosixPath]]:
    source = PROJECT / "src" / "semantic_normalizer"
    data_files = set(package_data_files())
    selected = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"symbolic link rejected from package: {path}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.suffix != ".py" and path not in data_files:
            continue
        selected.append((path, PurePosixPath("semantic_normalizer") / path.relative_to(source)))
    if not selected:
        raise BuildError("package payload is empty")
    return selected


def metadata_files() -> dict[PurePosixPath, bytes]:
    metadata = (
        "Metadata-Version: 2.4\n"
        "Name: semantic-normalizer\n"
        f"Version: {VERSION}\n"
        "Summary: Deterministic bilingual semantic normalization and reconciliation\n"
        "Requires-Python: >=3.14\n"
        "License: Proprietary\n"
        "\n"
    ).encode()
    wheel = (
        "Wheel-Version: 1.0\n"
        "Generator: semantic-normalizer-offline-builder\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
        "\n"
    ).encode()
    entry_points = (
        "[console_scripts]\n"
        "semantic-normalizer = semantic_normalizer.cli:main\n"
    ).encode()
    return {
        PurePosixPath(DIST_INFO) / "METADATA": metadata,
        PurePosixPath(DIST_INFO) / "WHEEL": wheel,
        PurePosixPath(DIST_INFO) / "entry_points.txt": entry_points,
    }


def record_bytes(files: dict[PurePosixPath, bytes]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name in sorted(files, key=str):
        data = files[name]
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([str(name), f"sha256={encoded}", len(data)])
    writer.writerow([f"{DIST_INFO}/RECORD", "", ""])
    return output.getvalue().encode()


def stage_wheel(root: Path) -> None:
    files = {
        destination: source.read_bytes()
        for source, destination in package_files()
    }
    files.update(metadata_files())
    files[PurePosixPath(DIST_INFO) / "RECORD"] = record_bytes(files)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def canonical_zip(
    destination: Path,
    files: dict[PurePosixPath, bytes],
    *,
    prefix: str | None = None,
) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True
    ) as archive:
        for relative in sorted(files, key=str):
            name = str(PurePosixPath(prefix) / relative) if prefix else str(relative)
            if "heldout" in PurePosixPath(name).parts:
                raise BuildError(f"heldout path rejected from archive: {name}")
            info = zipfile.ZipInfo(name, FIXED_DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits = 0
            archive.writestr(info, files[relative])


def build_one_wheel(work: Path) -> tuple[Path, str]:
    staging = work / "staging"
    packed = work / "packed"
    staging.mkdir(parents=True)
    packed.mkdir()
    stage_wheel(staging)
    environment = {**os.environ, "PIP_NO_INDEX": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    run(
        [sys.executable, "-m", "wheel", "pack", str(staging), "--dest-dir", str(packed)],
        cwd=PROJECT,
        env=environment,
    )
    generated = packed / WHEEL_NAME
    if not generated.is_file():
        raise BuildError(f"wheel pack did not create {WHEEL_NAME}")
    with zipfile.ZipFile(generated) as archive:
        files = {
            PurePosixPath(info.filename): archive.read(info)
            for info in archive.infolist()
            if not info.is_dir()
        }
    canonical = work / WHEEL_NAME
    canonical_zip(canonical, files)
    return canonical, file_digest(canonical)


def validate_record(archive: zipfile.ZipFile) -> int:
    record_name = f"{DIST_INFO}/RECORD"
    rows = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
    expected_names = {info.filename for info in archive.infolist() if not info.is_dir()}
    if {row[0] for row in rows} != expected_names:
        raise BuildError("wheel RECORD member set differs from archive")
    for name, hash_field, size_field in rows:
        if name == record_name:
            if hash_field or size_field:
                raise BuildError("RECORD self-entry must omit hash and size")
            continue
        data = archive.read(name)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if hash_field != f"sha256={encoded}" or size_field != str(len(data)):
            raise BuildError(f"invalid RECORD entry: {name}")
    return len(rows)


def validate_wheel(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        member_names = {info.filename for info in infos}
        if infos != sorted(infos, key=lambda info: info.filename):
            raise BuildError("wheel members are not sorted")
        missing = sorted(REQUIRED_WHEEL_MEMBERS - member_names)
        if missing:
            raise BuildError(f"wheel misses public runtime contracts: {missing}")
        forbidden_members = sorted(
            name
            for name in member_names
            if (
                FORBIDDEN_WHEEL_PARTS.intersection(
                    part.casefold() for part in PurePosixPath(name).parts
                )
                or PurePosixPath(name).suffix.casefold() in FORBIDDEN_WHEEL_SUFFIXES
            )
        )
        if forbidden_members:
            raise BuildError(
                f"wheel contains forbidden non-runtime payload: {forbidden_members}"
            )
        for info in infos:
            if info.date_time != FIXED_DATE or info.compress_type != zipfile.ZIP_STORED:
                raise BuildError(f"wheel metadata is not canonical: {info.filename}")
            if (info.external_attr >> 16) & 0o777 != 0o644:
                raise BuildError(f"wheel mode is not 0644: {info.filename}")
        metadata = archive.read(f"{DIST_INFO}/METADATA").decode()
        wheel = archive.read(f"{DIST_INFO}/WHEEL").decode()
        entries = archive.read(f"{DIST_INFO}/entry_points.txt").decode()
        if "Requires-Python: >=3.14" not in metadata:
            raise BuildError("wheel METADATA lacks Requires-Python >=3.14")
        if "Tag: py3-none-any" not in wheel:
            raise BuildError("wheel tag is invalid")
        if "semantic-normalizer = semantic_normalizer.cli:main" not in entries:
            raise BuildError("console entry point is invalid")
        governed_data_hashes = {}
        for source, relative in package_files():
            name = str(relative)
            if archive.read(name) != source.read_bytes():
                raise BuildError(f"source/wheel byte mismatch: {name}")
            if (
                relative.parts[:2] == ("semantic_normalizer", "data")
                and source.suffix != ".py"
            ):
                governed_data_hashes[source.name] = file_digest(source)
        record_count = validate_record(archive)
        return {
            "members": len(infos),
            "record_entries": record_count,
            "timestamps": "1980-01-01T00:00:00",
            "mode": "0644",
            "compression": "ZIP_STORED",
            "requires_python": ">=3.14",
            "entry_point": "semantic-normalizer = semantic_normalizer.cli:main",
            "source_data_members_equal": True,
            "governed_data_sha256": dict(sorted(governed_data_hashes.items())),
            "public_contract_members": sorted(REQUIRED_WHEEL_MEMBERS),
            "forbidden_non_runtime_members": [],
        }


def tree_hash(root: Path, *, exclude_caches: bool = False) -> str:
    stream = bytearray()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            if exclude_caches and (
                "__pycache__" in path.parts or path.suffix == ".pyc"
            ):
                continue
            stream.extend(str(path.relative_to(root)).encode())
            stream.extend(b"\0")
            stream.extend(path.read_bytes())
            stream.extend(b"\0")
    return digest(bytes(stream))


def tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): file_digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def validate_install(wheel: Path, work: Path) -> dict:
    venv = work / "venv"
    workspace = work / "external-workspace"
    run([sys.executable, "-m", "venv", str(venv)])
    python = venv / "bin" / "python"
    pip = venv / "bin" / "pip"
    console = venv / "bin" / "semantic-normalizer"
    environment = {
        **os.environ,
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    install = run(
        [
            str(pip), "install", "--no-index", "--no-deps",
            "--disable-pip-version-check", str(wheel),
        ],
        env=environment,
    )
    check = run([str(pip), "check"], env=environment)
    purelib = Path(run(
        [str(python), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        env=environment,
    ).stdout.strip())
    before_snapshot = tree_snapshot(purelib)
    before = tree_hash(purelib)
    installed_package_hash = tree_hash(
        purelib / "semantic_normalizer", exclude_caches=True
    )
    help_result = run([str(console), "--help"], env=environment)
    validate = run([str(console), "validate-registry"], env=environment)
    normalize = run([
        str(console), "normalize", "--text",
        "O operador deve começar o servidor APP-01.", "--kind", "text",
    ], env=environment)
    init = run([str(console), "init-workspace", str(workspace)], env=environment)
    workspace_seed_snapshot = {
        name: file_digest(workspace / name)
        for name in ("registry.jsonl", "registry.schema.json")
    }
    request_result = run([
        str(console), "reconcile-request", "--workspace", str(workspace),
        "--context", "Check the base.", "--start", "10", "--end", "14",
        "--language", "en",
    ], env=environment)
    request_path = work / "reconciliation-request.json"
    response_path = work / "reconciliation-response.json"
    request_path.write_text(request_result.stdout, encoding="utf-8")
    response_path.write_text(json.dumps({
        "candidate_id": "entity.facility_base",
        "confidence": 0.9,
        "evidence_span": {"start": 10, "end": 14, "text": "base"},
        "reason_code": "CONTEXT_DISAMBIGUATED",
    }), encoding="utf-8")
    apply_result = run([
        str(console), "reconcile-apply", "--workspace", str(workspace),
        "--request", str(request_path), "--response", str(response_path),
        "--reviewer", "release-smoke",
        "--rationale", "installed runtime registry-derived ambiguity smoke",
        "--protected-slot-comparison", "not-applicable",
        "--timestamp", "2026-07-30T00:00:00Z",
    ], env=environment)
    data_names = governed_data_names()
    data_check = run(
        [
            str(python), "-c",
            (
                "from importlib.resources import files;"
                "from pathlib import Path;"
                "import hashlib,json;"
                "d=files('semantic_normalizer.data');"
                f"names={json.dumps(data_names)};"
                "print(json.dumps({n:hashlib.sha256(d.joinpath(n).read_bytes()).hexdigest()"
                " for n in names},sort_keys=True))"
            ),
        ],
        env=environment,
    )
    public_contract_smoke = run(
        [
            str(python),
            "-c",
            """
import json

from semantic_normalizer.normalizer import normalize_text
from semantic_normalizer.registry import (
    ContractError,
    load_lexicon,
    load_registry,
    package_data,
)
from semantic_normalizer.schema_validation import load_package_schema, validate_instance

# The installed wheel must find its own registry, not the source tree's.
registry = load_registry()
if len(registry["records"]) < 1:
    raise AssertionError("packaged registry is empty")

release = json.loads(package_data("registry.release.json").read_text(encoding="utf-8"))
if release["version"] != registry["version"]:
    raise AssertionError(
        f"release record {release['version']} does not govern registry {registry['version']}"
    )
if sorted(release["affected_concepts"]) != sorted(registry["by_id"]):
    raise AssertionError("release record does not describe the packaged registry")

provenance = [
    json.loads(line)
    for line in package_data("registry.provenance.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]
if provenance[-1]["target"]["registry_version"] != registry["version"]:
    raise AssertionError("provenance ledger does not end at the packaged registry version")
if len({event["event_id"] for event in provenance}) != len(provenance):
    raise AssertionError("provenance ledger has a duplicated event id")

# The runtime works from the installed package, and still refuses to accept silently.
lexicon = load_lexicon()
records = normalize_text(
    "O operador deve comecar o servidor APP-01.", "wheel-smoke", "text", lexicon
)
if not records:
    raise AssertionError("normalize_text produced no record")
record = records[0]
if record["original_text"] != "O operador deve comecar o servidor APP-01.":
    raise AssertionError("normalize_text mutated the source")
if "APP-01" not in [item["value"] for item in record["protected_values"]]:
    raise AssertionError("identifier APP-01 was not protected")
unknown = normalize_text("Xyzzy plugh frobnicate.", "wheel-smoke", "text", lexicon)[0]
if unknown["canonical_status"] == "accepted":
    raise AssertionError("unknown vocabulary was silently accepted")

schemas = ('registry.schema.json', 'sidecar.schema.json', 'reconciliation-request.schema.json', 'reconciliation-response.schema.json', 'reconciliation-decision.schema.json')
for name in schemas:
    schema = load_package_schema(name)
    try:
        validate_instance({}, schema)
    except ContractError:
        pass
    else:
        raise AssertionError(f"{name} accepted an empty instance")

print(json.dumps({
    "registry_records": len(registry["records"]),
    "registry_version": registry["version"],
    "release_governs_registry": True,
    "provenance_events": len(provenance),
    "source_preserved": True,
    "identifier_protected": True,
    "unknown_vocabulary_status": unknown["canonical_status"],
    "schemas": list(schemas),
}, sort_keys=True))
""",
        ],
        env=environment,
    )
    after_snapshot = tree_snapshot(purelib)
    after = tree_hash(purelib)
    changed_site_packages = sorted(
        name
        for name in set(before_snapshot) | set(after_snapshot)
        if before_snapshot.get(name) != after_snapshot.get(name)
    )
    if before != after:
        raise BuildError("site-packages changed during runtime smoke commands")
    source_hashes = {
        name: file_digest(PROJECT / "src" / "semantic_normalizer" / "data" / name)
        for name in data_names
    }
    if json.loads(data_check.stdout) != source_hashes:
        raise BuildError("installed importlib.resources data differs from source")
    contract_report = json.loads(public_contract_smoke.stdout)
    if (
        contract_report["registry_records"] < 1
        or not contract_report["release_governs_registry"]
        or contract_report["provenance_events"] < 1
        or contract_report["registry_version"] != registry_version()
        or not contract_report["source_preserved"]
        or not contract_report["identifier_protected"]
        # The invariant the whole 0.3.0 promotion exists for: unknown vocabulary must never
        # come back `accepted`.
        or contract_report["unknown_vocabulary_status"] == "accepted"
        or tuple(contract_report["schemas"]) != PUBLIC_CONTRACT_SCHEMAS
    ):
        raise BuildError("installed public contract smoke failed")
    normalized = json.loads(normalize.stdout)
    if (
        normalized["canonical_status"] != "review"
        or normalized["original_text"] != "O operador deve começar o servidor APP-01."
        or normalized["protected_values"][0]["value"] != "APP-01"
    ):
        raise BuildError("installed canonical example failed")
    workspace_report = json.loads(init.stdout)
    if set(workspace_report["files"]) != {
        "registry.jsonl", "registry.schema.json", "candidates.jsonl", "decisions.jsonl"
    }:
        raise BuildError("installed init-workspace output is invalid")
    request = json.loads(request_result.stdout)
    decision = json.loads(apply_result.stdout)
    candidates = (workspace / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    decisions = (workspace / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    installed_mutable_ledgers = sorted(
        path.relative_to(purelib).as_posix()
        for path in purelib.rglob("*")
        if path.is_file() and path.name in {"candidates.jsonl", "decisions.jsonl"}
    )
    if (
        request["allowed_candidates"]
        != ["entity.facility_base", "technical.numeral_base"]
        or decision["result"]["state"] != "accepted"
        or len(candidates) != 1
        or len(decisions) != 1
        or workspace_seed_snapshot != {
            name: file_digest(workspace / name)
            for name in ("registry.jsonl", "registry.schema.json")
        }
        or workspace.is_relative_to(purelib)
        or installed_mutable_ledgers
    ):
        raise BuildError("installed external reconciliation workspace smoke failed")
    return {
        "pip_install": "PASS" if "Successfully installed" in install.stdout else "PASS",
        "pip_check": check.stdout.strip(),
        "console_help": "usage: semantic-normalizer" in help_result.stdout,
        "validate_registry": json.loads(validate.stdout),
        "canonical_example": {
            "canonical_status": normalized["canonical_status"],
            "protected_value": normalized["protected_values"][0]["value"],
            # Registry 2.1.0 imported action.start, so resolving it is correct behaviour,
            # not invention. entity.server has never existed (the concept is system.server)
            # and stays an invention guard.
            "resolves_action_start": "action.start" in normalized["concept_ids"],
            "invented_entity_server": "entity.server" in normalized["concept_ids"],
        },
        "init_workspace": workspace_report["files"],
        "reconciliation": {
            "allowed_candidates": request["allowed_candidates"],
            "state": decision["result"]["state"],
            "candidate_ledger_records": len(candidates),
            "decision_ledger_records": len(decisions),
            "workspace_external_to_site_packages": not workspace.is_relative_to(purelib),
            "registry_seed_unchanged": True,
            "mutable_ledgers_in_site_packages": installed_mutable_ledgers,
        },
        "site_packages_snapshot": "complete-tree SHA-256 compared before and after runtime commands",
        "site_packages_unchanged": before == after,
        "site_packages_files_compared": len(before_snapshot),
        "site_packages_changed_files": changed_site_packages,
        "installed_package_sha256": installed_package_hash,
        "installed_data_equal_source": True,
        "public_contract_smoke": contract_report,
    }


def public_report_files() -> list[Path]:
    excluded = {
        "package-validation.json",
        "release-manifest.json",
    }
    observed_jsonl = {path.name for path in REPORTS.glob("*.jsonl")}
    if observed_jsonl != EXCLUDED_REPORT_JSONL:
        raise BuildError(
            "reports JSONL exclusion contract drift: "
            f"expected={sorted(EXCLUDED_REPORT_JSONL)}, actual={sorted(observed_jsonl)}"
        )
    result = []
    for path in sorted(REPORTS.glob("*.json")):
        if path.name not in excluded:
            result.append(path)
    return result


def skill_payload(wheel: Path, include_generated_reports: bool) -> dict[PurePosixPath, bytes]:
    files: dict[PurePosixPath, bytes] = {}
    # 0.3.0 ships the skill's own documentation set; the artifact's STE chapter/glossary
    # payload stayed behind with the held-out custody it belonged to.
    roots = [
        PROJECT / "SKILL.md", PROJECT / "README.md", PROJECT / "CHANGELOG.md",
        PROJECT / "LICENSE", PROJECT / "pyproject.toml",
    ]
    for path in roots:
        if not path.is_file():
            raise BuildError(f"release ZIP payload is missing {path.name}")
        files[PurePosixPath(path.name)] = path.read_bytes()
    for directory in ("docs", "prompts", "schemas", "src", "scripts", "tests"):
        base = PROJECT / directory
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise BuildError(f"symbolic link rejected from release ZIP: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(PROJECT)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if "heldout" in relative.parts or ".venv" in relative.parts:
                continue
            files[PurePosixPath(relative.as_posix())] = path.read_bytes()
    for report in public_report_files():
        files[PurePosixPath("reports") / report.name] = report.read_bytes()
    if include_generated_reports:
        for report in (PACKAGE_REPORT, RELEASE_MANIFEST):
            files[PurePosixPath("reports") / report.name] = report.read_bytes()
    files[PurePosixPath("dist") / wheel.name] = wheel.read_bytes()
    forbidden = EXCLUDED_REPORT_JSONL | {"candidates.jsonl", "decisions.jsonl"}
    secret_names = {
        ".env", "id_rsa", "id_ed25519", "credentials", "credentials.json",
        "secrets", "secrets.json",
    }
    for relative in files:
        lowered = {part.casefold() for part in relative.parts}
        if (
            forbidden.intersection(relative.parts)
            or "heldout" in lowered
            or secret_names.intersection(lowered)
            or relative.suffix.casefold() in {".pem", ".key", ".p12", ".pfx"}
        ):
            raise BuildError(f"forbidden release payload: {relative}")
    return files


def create_release_manifest(wheel: Path, package_report: dict) -> dict:
    payload = skill_payload(wheel, include_generated_reports=False)
    payload[PurePosixPath("reports/package-validation.json")] = (
        PACKAGE_REPORT.read_bytes()
    )
    entries = [
        {
            "path": str(path),
            "sha256": digest(content),
            "size": len(content),
        }
        for path, content in sorted(payload.items(), key=lambda item: str(item[0]))
    ]
    return {
        "manifest_version": "1.0.0",
        "release_version": VERSION,
        "archive_prefix": ZIP_PREFIX,
        "payload": entries,
        "payload_files": len(entries),
        "wheel_sha256": file_digest(wheel),
        "package_validation_sha256": digest(json_bytes(package_report)),
        "excluded_from_internal_hashes": [ZIP_NAME, "checksums.sha256", "reports/release-manifest.json"],
        "exclusions": [
            "heldout", "__pycache__", "*.pyc", ".venv", "workspace",
            "candidates.jsonl", "decisions.jsonl",
            *sorted(f"reports/{name}" for name in EXCLUDED_REPORT_JSONL),
        ],
    }


def build_one_skill_zip(wheel: Path, destination: Path) -> tuple[str, int]:
    payload = skill_payload(wheel, include_generated_reports=True)
    canonical_zip(destination, payload, prefix=ZIP_PREFIX)
    return file_digest(destination), len(payload)


def validate_skill_zip(path: Path, expected_payload_files: int) -> dict:
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) != expected_payload_files:
            raise BuildError("skill ZIP payload count differs")
        prefixes = {PurePosixPath(info.filename).parts[0] for info in infos}
        if prefixes != {ZIP_PREFIX}:
            raise BuildError("skill ZIP does not have exactly one prefix")
        for info in infos:
            parts = PurePosixPath(info.filename).parts
            if (
                "heldout" in parts or "__pycache__" in parts
                or info.filename.endswith(".pyc")
                or info.date_time != FIXED_DATE
                or info.compress_type != zipfile.ZIP_STORED
                or (info.external_attr >> 16) & 0o777 != 0o644
            ):
                raise BuildError(f"invalid skill ZIP member: {info.filename}")
        manifest_name = f"{ZIP_PREFIX}/reports/release-manifest.json"
        manifest = json.loads(archive.read(manifest_name))
        listed = {item["path"]: item for item in manifest["payload"]}
        actual = {
            str(PurePosixPath(info.filename).relative_to(ZIP_PREFIX))
            for info in infos
            if info.filename != manifest_name
        }
        if set(listed) != actual:
            raise BuildError("internal manifest does not cover the exact ZIP payload")
        for relative, entry in listed.items():
            data = archive.read(f"{ZIP_PREFIX}/{relative}")
            if digest(data) != entry["sha256"] or len(data) != entry["size"]:
                raise BuildError(f"internal manifest mismatch: {relative}")
        return {
            "members": len(infos),
            "prefix": ZIP_PREFIX,
            "timestamps": "1980-01-01T00:00:00",
            "mode": "0644",
            "compression": "ZIP_STORED",
            "heldout_members": 0,
            "cache_members": 0,
            "manifest_entries_verified": len(listed),
        }


def write_checksums(paths: list[Path]) -> None:
    lines = [
        f"{file_digest(path)}  {path.relative_to(PROJECT).as_posix()}"
        for path in sorted(paths, key=lambda item: item.relative_to(PROJECT).as_posix())
    ]
    CHECKSUMS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_release() -> dict:
    environment = assert_environment()
    DIST.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="semantic-normalizer-release-") as directory:
        temporary = Path(directory)
        wheel_a, hash_a = build_one_wheel(temporary / "wheel-a")
        wheel_b, hash_b = build_one_wheel(temporary / "wheel-b")
        if wheel_a.read_bytes() != wheel_b.read_bytes() or hash_a != hash_b:
            raise BuildError("double wheel build is not byte-identical")
        wheel = DIST / WHEEL_NAME
        shutil.copyfile(wheel_a, wheel)
        wheel_validation = validate_wheel(wheel)
        install_validation = validate_install(wheel, temporary / "install")
        package_report = {
            "report_version": "1.0.0",
            "release_version": VERSION,
            "environment": environment,
            "commands": [
                "python3 -m wheel pack <staging> --dest-dir <packed>",
                (
                    "PIP_NO_INDEX=1 <venv>/bin/pip install --no-index --no-deps "
                    "--disable-pip-version-check "
                    "dist/semantic_normalizer-2.0.0-py3-none-any.whl"
                ),
                "PIP_NO_INDEX=1 <venv>/bin/pip check",
                "PYTHONDONTWRITEBYTECODE=1 <venv>/bin/semantic-normalizer --help",
                (
                    "PYTHONDONTWRITEBYTECODE=1 "
                    "<venv>/bin/semantic-normalizer validate-registry"
                ),
                (
                    "PYTHONDONTWRITEBYTECODE=1 <venv>/bin/semantic-normalizer "
                    "normalize --text \"O operador deve começar o servidor APP-01.\" "
                    "--kind text"
                ),
                (
                    "PYTHONDONTWRITEBYTECODE=1 <venv>/bin/semantic-normalizer "
                    "init-workspace <external-workspace>"
                ),
                (
                    "PYTHONDONTWRITEBYTECODE=1 <venv>/bin/semantic-normalizer "
                    "reconcile-request --workspace <external-workspace> "
                    "--context \"Check the base.\" --start 10 --end 14 --language en"
                ),
                (
                    "PYTHONDONTWRITEBYTECODE=1 <venv>/bin/semantic-normalizer "
                    "reconcile-apply --workspace <external-workspace> "
                    "--request <request.json> --response <response.json> "
                    "--reviewer release-smoke --rationale <rationale> "
                    "--protected-slot-comparison not-applicable"
                ),
                (
                    "PYTHONDONTWRITEBYTECODE=1 <venv>/bin/python -c "
                    "<public-contract-smoke-without-heldout-corpus>"
                ),
            ],
            "wheel": {
                "path": f"dist/{WHEEL_NAME}",
                "sha256": file_digest(wheel),
                "size": wheel.stat().st_size,
                "double_build_byte_identical": True,
                **wheel_validation,
            },
            "offline_install": install_validation,
        }
        PACKAGE_REPORT.write_bytes(json_bytes(package_report))
        manifest = create_release_manifest(wheel, package_report)
        RELEASE_MANIFEST.write_bytes(json_bytes(manifest))
        zip_a = temporary / f"a-{ZIP_NAME}"
        zip_b = temporary / f"b-{ZIP_NAME}"
        zip_hash_a, payload_files_a = build_one_skill_zip(wheel, zip_a)
        zip_hash_b, payload_files_b = build_one_skill_zip(wheel, zip_b)
        if (
            zip_a.read_bytes() != zip_b.read_bytes()
            or zip_hash_a != zip_hash_b
            or payload_files_a != payload_files_b
        ):
            raise BuildError("double skill ZIP build is not byte-identical")
        skill_zip = DIST / ZIP_NAME
        shutil.copyfile(zip_a, skill_zip)
        zip_validation = validate_skill_zip(skill_zip, payload_files_a)
        package_report["skill_zip"] = {
            "path": f"dist/{ZIP_NAME}",
            "hash_location": "checksums.sha256 (external)",
            "double_build_byte_identical": True,
            **zip_validation,
        }
        # Updating the report changes ZIP payload. Rebuild A/B once with the
        # final report, then validate the final bytes.
        PACKAGE_REPORT.write_bytes(json_bytes(package_report))
        manifest = create_release_manifest(wheel, package_report)
        RELEASE_MANIFEST.write_bytes(json_bytes(manifest))
        final_a = temporary / f"final-a-{ZIP_NAME}"
        final_b = temporary / f"final-b-{ZIP_NAME}"
        final_hash_a, final_count_a = build_one_skill_zip(wheel, final_a)
        final_hash_b, final_count_b = build_one_skill_zip(wheel, final_b)
        if final_a.read_bytes() != final_b.read_bytes() or final_hash_a != final_hash_b:
            raise BuildError("final double skill ZIP build is not byte-identical")
        shutil.copyfile(final_a, skill_zip)
        final_zip_validation = validate_skill_zip(skill_zip, final_count_a)
        if final_zip_validation != zip_validation:
            raise BuildError("final skill ZIP validation changed unexpectedly")
    checksum_targets = [
        wheel,
        skill_zip,
        PACKAGE_REPORT,
        RELEASE_MANIFEST,
        *public_report_files(),
    ]
    write_checksums(list(dict.fromkeys(checksum_targets)))
    return {
        "wheel": {
            "path": str(wheel.relative_to(PROJECT)),
            "sha256": file_digest(wheel),
            "size": wheel.stat().st_size,
        },
        "skill_zip": {
            "path": str(skill_zip.relative_to(PROJECT)),
            "sha256": file_digest(skill_zip),
            "size": skill_zip.stat().st_size,
        },
        "package_report": str(PACKAGE_REPORT.relative_to(PROJECT)),
        "release_manifest": str(RELEASE_MANIFEST.relative_to(PROJECT)),
        "checksums": str(CHECKSUMS.relative_to(PROJECT)),
        "reproducible": True,
        "offline": True,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--json", action="store_true", help="Emit the final result as JSON."
    )
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        result = build_release()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            for key, value in result.items():
                print(f"{key}: {value}")
        return 0
    except (BuildError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
