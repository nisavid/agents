"""Derive the minimal Phase 7 public/private compatibility document."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, NoReturn, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_transport import (  # noqa: E402
    candidate_content_identity,
    json_file_bytes,
    strict_json_bytes,
)


COMPATIBILITY_CONTRACT = "phase7-public-private-compatibility-v5"
COMPATIBILITY_SCHEMA_VERSION = 5
MAX_SOURCE_BYTES = 2 * 1024 * 1024

ROLECASTING_TOPOLOGY = Path("plugins/rolecasting/topology.json")
ROLECASTING_PROVIDER = Path("plugins/rolecasting/task-witness-provider.json")
VERSIONKEEPING_TOPOLOGY = Path("plugins/versionkeeping/topology.json")
TRICRITICAL_TOPOLOGY = Path("plugins/tricritical/topology.json")
TRICRITICAL_PROVIDER = Path("plugins/tricritical/task-witness-provider.json")
REVIEW_ATLAS_EXTENSION = Path(
    "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/"
    "references/review-atlas-extension.json"
)
REVIEW_ATLAS_CONTRACT = Path("release/mergecraft/review-atlas-contract.json")
TRICRITICAL_SKILLS = (
    "review",
    "intent",
    "runtime",
    "structure",
    "adjudicate",
    "revise",
    "loop",
)
TRICRITICAL_AUTHORITY_FIELDS = (
    "calls",
    "mutates_directly",
    "can_cause_mutation",
    "requires_original_mutation_authority",
    "requires",
)
REVIEW_ATLAS_EXTENSION_FIELDS = (
    "load_condition",
    "allowed_authority",
    "forbidden_authority",
    "precedence",
)
TASK_WITNESS_PROVIDER_FIELDS = (
    "schema_version",
    "contract",
    "plugin_id",
    "publisher",
    "repository",
    "authority_profile",
    "content_sha256",
    "producers",
    "issuers",
    "validators",
)
ROLECASTING_ASSURANCE_DIMENSIONS = (
    "target",
    "model",
    "topology",
    "authority",
    "execution_result",
)
ROLECASTING_ASSURANCE_LEVELS = (
    "product-attested",
    "controller-observed",
    "self-reported",
)
ROLECASTING_ASSURANCE_STRENGTH_ORDER = (
    "self-reported",
    "controller-observed",
    "product-attested",
)

# This selector document is also the registry-facing coordination contract.
SOURCE_SELECTORS = (
    {
        "path": ROLECASTING_TOPOLOGY.as_posix(),
        "json_pointers": ("/receipt_contract",),
    },
    {
        "path": ROLECASTING_PROVIDER.as_posix(),
        "json_pointers": tuple(f"/{field}" for field in TASK_WITNESS_PROVIDER_FIELDS),
    },
    {
        "path": VERSIONKEEPING_TOPOLOGY.as_posix(),
        "json_pointers": ("/operation_owners", "/terminal_handoff"),
    },
    {
        "path": TRICRITICAL_TOPOLOGY.as_posix(),
        "json_pointers": tuple(
            f"/skills/{skill}/{field}"
            for skill in TRICRITICAL_SKILLS
            for field in TRICRITICAL_AUTHORITY_FIELDS
        ),
    },
    {
        "path": TRICRITICAL_PROVIDER.as_posix(),
        "json_pointers": tuple(f"/{field}" for field in TASK_WITNESS_PROVIDER_FIELDS),
    },
    {
        "path": REVIEW_ATLAS_EXTENSION.as_posix(),
        "json_pointers": tuple(
            f"/{field}" for field in REVIEW_ATLAS_EXTENSION_FIELDS
        ),
    },
    {
        "path": REVIEW_ATLAS_CONTRACT.as_posix(),
        "json_pointers": ("/firewall",),
    },
)


class CompatibilityProjectionError(RuntimeError):
    pass


def _fail(message: str) -> NoReturn:
    raise CompatibilityProjectionError(message)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _field(document: dict[str, Any], field: str, label: str) -> Any:
    if field not in document:
        _fail(f"{label} is missing {field}")
    return document[field]


def _document(root: Path, relative: Path, label: str) -> dict[str, Any]:
    if not root.is_absolute():
        _fail("public candidate root must be absolute")
    path = root / relative
    try:
        root_resolved = root.resolve(strict=True)
        path_resolved = path.resolve(strict=True)
        path_resolved.relative_to(root_resolved)
    except (OSError, ValueError) as error:
        raise CompatibilityProjectionError(
            f"{label} is outside or absent from the public candidate"
        ) from error
    if path.is_symlink() or not path_resolved.is_file():
        _fail(f"{label} must be a regular non-symlink file")
    content = path_resolved.read_bytes()
    if not content or len(content) > MAX_SOURCE_BYTES:
        _fail(f"{label} has an invalid size")
    return _object(
        strict_json_bytes(
            content,
            label=label,
            error_factory=CompatibilityProjectionError,
        ),
        label,
    )


def _provider_projection(document: dict[str, Any], label: str) -> dict[str, Any]:
    """Project the complete authority-bearing Task Witness declaration fields."""

    return {
        field: _field(document, field, label)
        for field in TASK_WITNESS_PROVIDER_FIELDS
    }


def compatibility_document(root: Path) -> dict[str, Any]:
    """Project only authority consumed across the private deployment boundary."""

    rolecasting = _document(root, ROLECASTING_TOPOLOGY, "Rolecasting topology")
    rolecasting_provider = _document(
        root,
        ROLECASTING_PROVIDER,
        "Rolecasting Task Witness provider",
    )
    versionkeeping = _document(
        root, VERSIONKEEPING_TOPOLOGY, "Versionkeeping topology"
    )
    tricritical = _document(root, TRICRITICAL_TOPOLOGY, "Tricritical topology")
    tricritical_provider = _document(
        root,
        TRICRITICAL_PROVIDER,
        "Tricritical Task Witness provider",
    )
    extension = _document(
        root, REVIEW_ATLAS_EXTENSION, "Review Atlas extension contract"
    )
    atlas = _document(root, REVIEW_ATLAS_CONTRACT, "Review Atlas release contract")

    tricritical_skills = _object(
        _field(tricritical, "skills", "Tricritical topology"),
        "Tricritical skills",
    )
    projected_skills: dict[str, dict[str, Any]] = {}
    for skill in TRICRITICAL_SKILLS:
        skill_contract = _object(
            _field(tricritical_skills, skill, "Tricritical skills"),
            f"Tricritical {skill} contract",
        )
        projected_skills[skill] = {
            field: _field(
                skill_contract,
                field,
                f"Tricritical {skill} contract",
            )
            for field in TRICRITICAL_AUTHORITY_FIELDS
        }

    return {
        "schema_version": COMPATIBILITY_SCHEMA_VERSION,
        "contract": COMPATIBILITY_CONTRACT,
        "rolecasting": {
            "receipt_contract": _object(
                _field(rolecasting, "receipt_contract", "Rolecasting topology"),
                "Rolecasting receipt contract",
            ),
            "assurance_contract": {
                "dimensions": list(ROLECASTING_ASSURANCE_DIMENSIONS),
                "levels": list(ROLECASTING_ASSURANCE_LEVELS),
                "strength_order": list(ROLECASTING_ASSURANCE_STRENGTH_ORDER),
                "implicit_promotion": "forbidden",
            },
            "task_witness_provider": _provider_projection(
                rolecasting_provider,
                "Rolecasting Task Witness provider",
            ),
        },
        "versionkeeping": {
            "operation_owners": _object(
                _field(
                    versionkeeping,
                    "operation_owners",
                    "Versionkeeping topology",
                ),
                "Versionkeeping operation owners",
            ),
            "terminal_handoff": _object(
                _field(
                    versionkeeping,
                    "terminal_handoff",
                    "Versionkeeping topology",
                ),
                "Versionkeeping terminal handoff",
            ),
        },
        "tricritical": {
            "skills": projected_skills,
            "task_witness_provider": _provider_projection(
                tricritical_provider,
                "Tricritical Task Witness provider",
            ),
        },
        "review_atlas": {
            "extension": {
                field: _field(
                    extension,
                    field,
                    "Review Atlas extension contract",
                )
                for field in REVIEW_ATLAS_EXTENSION_FIELDS
            },
            "release_runtime_ownership": _object(
                _field(atlas, "firewall", "Review Atlas release contract"),
                "Review Atlas release/runtime ownership",
            ),
        },
    }


def compatibility_bytes(root: Path) -> bytes:
    """Return the newline-terminated canonical compatibility document bytes."""

    return json_file_bytes(compatibility_document(root))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive the Phase 7 public/private compatibility document."
    )
    parser.add_argument(
        "--public-root",
        required=True,
        type=Path,
        help="Absolute path to the public candidate root.",
    )
    parser.add_argument("--expected-public-candidate-sha256", required=True)
    arguments = parser.parse_args(argv)

    try:
        if candidate_content_identity(
            arguments.public_root, error_factory=CompatibilityProjectionError
        ) != arguments.expected_public_candidate_sha256:
            raise CompatibilityProjectionError("public candidate identity mismatch")
        content = compatibility_bytes(arguments.public_root)
    except (CompatibilityProjectionError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    sys.stdout.buffer.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
