#!/usr/bin/env python3
"""Validate the Issue #50 source-skill disposition and refresh contracts."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sys
from datetime import datetime
from pathlib import Path


LINEAGE_ROOT = Path("release/source-skill-lineage")
DISPOSITION_ROOT = Path("release/source-skill-disposition")
SOURCE_MANIFEST = LINEAGE_ROOT / "source-manifest.json"
CONTRIBUTION_LEDGER = LINEAGE_ROOT / "contribution-ledger.json"
DISPOSITION_LEDGER = DISPOSITION_ROOT / "disposition-ledger.json"
REFRESH_CONTRACT = DISPOSITION_ROOT / "release-refresh-contract.json"
FINAL_RESCOUT_SCHEMA = (
    DISPOSITION_ROOT / "final-installed-library-rescout.schema.json"
)

ALLOWED_DISPOSITIONS = {
    "absorb-or-refresh",
    "defer-blocking",
    "defer-non-blocking",
    "equivalent-or-stronger",
    "reject",
    "retain-side-by-side",
    "split-by-component",
    "supersede-and-remove",
}
AUTHORITY = {
    "host_removal": "not-granted",
    "managed_source_mutation": "not-granted",
    "release_eligibility": "not-asserted",
}
LEDGER_LIMITATIONS = (
    "This ledger settles source contribution intent only. It does not mutate "
    "managed sources, authorize host installation or removal, assert release "
    "eligibility, or replace the final refresh and qualification owned by issue 45."
)
RESCOUT_SURFACES = [
    "external-tool-managed-skills",
    "global-agent-instructions",
    "plugin-provided-skills",
    "recursively-referenced-additional-instructions",
    "recursively-referenced-conditional-instructions",
    "recursively-referenced-supporting-materials",
    "skills-managed-skills",
    "standalone-skills",
]
RESCOUT_DISPOSITIONS = [
    "retain-as-is",
    "migrate-to-plugin-equipment",
    "migrate-to-external-personal-owner",
    "drop-with-reason",
]
INVALIDATION_TRIGGERS = [
    "candidate-identity-artifact",
    "candidate-package-byte",
    "contribution-decision",
    "installed-membership-or-identity",
    "instruction-discovery-route",
    "source-revision-tree-content-or-license",
]
WORKFLOW_STEPS = [
    "rescout-complete-installed-instruction-library",
    "refresh-upstream-and-source-identities",
    "three-way-reconcile-later-source-changes",
    "assign-a-terminal-disposition-to-every-material-contribution",
    "freeze-candidate-package-bytes",
    "evaluate-only-the-affected-dependency-closure",
    "capture-final-refresh-and-qualification-receipts",
]
DISPOSITION_INVENTORY_SHA256 = (
    "sha256:60954711bd16630cde718c3ae1eb7301087c261cbec3df2c31d0ce0ac136e597"
)
FRESHNESS = {
    "maximum_age_seconds": 86400,
    "measured_from": "completion of upstream refresh and installed-library rescout",
    "measured_to": "capture of the candidate qualification receipt",
}
FINAL_RESCOUT_ARTIFACT = {
    "contract": "coordinated-final-installed-library-rescout-v1",
    "path": "release/source-skill-lineage/installed-hosts/final-candidate-rescout-v1.json",
    "publication_receipt_binding": {
        "digest_contract": "raw-byte-sha256",
        "selector": "/",
    },
    "schema": {
        "path": FINAL_RESCOUT_SCHEMA.as_posix(),
        "sha256": "sha256:c1a1432331130d2c7504279cba44d841d14bc4535e6ca5fab557d3b1b066f592",
    },
    "validation_command": "uv run --with jsonschema==4.26.0 python scripts/validate_source_skill_disposition.py --require-final-rescout .",
}
FINAL_RESCOUT_INVARIANTS = [
    "started_at_utc <= profile_manifests[].observed_at_utc <= completed_at_utc",
    "started_at_utc <= completed_at_utc",
    "enumerated_profile_ids == sorted(profile_manifests[].profile_id)",
    "enumerated_profile_ids == sorted(unique(instruction_inventory.entries[].profile_id))",
    "instruction_inventory.surface_counts == counts(instruction_inventory.entries[].surface)",
    "instruction_inventory.sha256 == canonical_json_sha256(instruction_inventory.entries)",
    "content_sha256 == canonical_json_sha256(document excluding content_sha256)",
]
DISTRIBUTION_IDENTITIES = [
    {
        "id": "artifact-customs",
        "identity_artifact_paths": [
            "release/plugin-content-locks/artifact-customs.json"
        ],
        "plugin_root": "plugins/artifact-customs",
    },
    {
        "id": "mergecraft",
        "identity_artifact_paths": ["release/plugin-content-locks/mergecraft.json"],
        "plugin_root": "plugins/mergecraft",
    },
    {
        "id": "rolecasting",
        "identity_artifact_paths": ["plugins/rolecasting/content-lock.json"],
        "plugin_root": "plugins/rolecasting",
    },
    {
        "id": "task-witness",
        "identity_artifact_paths": [
            "release/task-witness/source-shape-review.json",
            "release/task-witness/tw4-suite-inventory.json",
        ],
        "plugin_root": "plugins/task-witness",
    },
    {
        "id": "tricritical",
        "identity_artifact_paths": ["plugins/tricritical/content-lock.json"],
        "plugin_root": "plugins/tricritical",
    },
    {
        "id": "versionkeeping",
        "identity_artifact_paths": [
            "release/plugin-content-locks/versionkeeping.json"
        ],
        "plugin_root": "plugins/versionkeeping",
    },
]
CANDIDATE_REPOSITORY_TUPLE = [
    "repository_id",
    "basis.commit_sha1",
    "basis.tree_sha1",
    "package_projection_contract",
    "packages_sha256",
]
CANDIDATE_PACKAGE_TUPLE = [
    "id",
    "version",
    "plugin_root",
    "git_tree_sha1",
    "package_tree_sha256",
    "plugin_manifest_sha256",
    "identity_artifacts[].path",
    "identity_artifacts[].sha256",
]
REGENERATION_OUTPUTS = [
    "affected-package-skills-tests-docs-and-identity-artifacts",
    "candidate-package-projections-and-source-manifest",
    "contribution-disposition-and-installed-host-manifest-bindings",
    "final-installed-library-rescout-artifact",
    "upstream-source-identity-and-license-evidence",
    "source-reconciliation-receipt",
    "plugin-evaluation-evidence-for-affected-closure",
    "independent-review-evidence-for-affected-closure",
    "candidate-qualification-evidence-for-affected-closure",
    "routing-and-composed-compatibility-evidence-for-affected-closure",
    "final-release-evidence",
]
DISPOSITION_ONLY_STEPS = [
    "rebind-contribution-disposition-and-installed-host-manifests",
    "regenerate-versioned-final-installed-library-rescout-artifact",
    "regenerate-source-reconciliation-receipt",
    "rerun-plugin-evaluation-for-affected-closure",
    "rerun-independent-review-for-affected-closure",
    "rerun-candidate-qualification-for-affected-closure",
    "rerun-routing-and-composed-compatibility-for-affected-closure",
    "regenerate-final-release-evidence",
]
PACKAGE_BYTE_STEPS = [
    "refresh-affected-package-skills-tests-docs-and-content-locks",
    "regenerate-candidate-package-projections-and-source-manifest",
    *DISPOSITION_ONLY_STEPS,
]
IDENTITY_ARTIFACT_STEPS = [
    "regenerate-candidate-package-projections-and-source-manifest",
    *DISPOSITION_ONLY_STEPS,
]
INSTALLED_LIBRARY_EVIDENCE_STEPS = [
    "rescout-complete-installed-instruction-library",
    "reevaluate-affected-contribution-dispositions",
    *DISPOSITION_ONLY_STEPS,
]
SOURCE_EVIDENCE_STEPS = [
    "refresh-upstream-source-tree-content-and-license-evidence",
    "three-way-reconcile-affected-source-contributions",
    "reevaluate-affected-contribution-dispositions",
    *DISPOSITION_ONLY_STEPS,
]
COMMON_DEPENDENCY_PATHS = [
    "evals/control-plane-matrix.json",
    "release/plugin-eval-baseline-v1.json",
    "release/plugin-eval-policy.json",
    "release/public-release-runtime-packages.json",
]
DEPENDENCY_EDGE_SEMANTICS = {
    "common_path_rule": "a-change-to-any-common-dependency-path-seeds-all-distributions",
    "direction": "provider-to-consumer",
    "seed_rule": "seed-each-distribution-owning-a-changed-package-support-or-identity-artifact-path",
    "traversal_rule": "include-seeds-then-repeatedly-include-every-consumer-whose-provider-is-in-the-closure-until-fixed-point",
}
DEPENDENCY_EDGES = [
    {
        "consumer": "artifact-customs",
        "evidence_path": "plugins/artifact-customs/topology.json",
        "provider": "mergecraft",
        "relationship": "external-call",
    },
    {
        "consumer": "artifact-customs",
        "evidence_path": "plugins/artifact-customs/topology.json",
        "provider": "rolecasting",
        "relationship": "external-call",
    },
    {
        "consumer": "artifact-customs",
        "evidence_path": "plugins/artifact-customs/topology.json",
        "provider": "tricritical",
        "relationship": "external-call",
    },
    {
        "consumer": "artifact-customs",
        "evidence_path": "plugins/artifact-customs/topology.json",
        "provider": "versionkeeping",
        "relationship": "external-call",
    },
    {
        "consumer": "mergecraft",
        "evidence_path": "plugins/mergecraft/topology.json",
        "provider": "task-witness",
        "relationship": "authenticated-process-profile",
    },
    {
        "consumer": "mergecraft",
        "evidence_path": "plugins/mergecraft/topology.json",
        "provider": "tricritical",
        "relationship": "imported-operation",
    },
    {
        "consumer": "mergecraft",
        "evidence_path": "plugins/mergecraft/topology.json",
        "provider": "versionkeeping",
        "relationship": "imported-operation",
    },
    {
        "consumer": "tricritical",
        "evidence_path": "plugins/tricritical/topology.json",
        "provider": "rolecasting",
        "relationship": "required-receipt",
    },
]
DISTRIBUTION_CLOSURE_SHA256 = (
    "sha256:23781e514c253b0347dcadc11f8a37c9dac8f44fd38a610d0d44b59ee5ed9b8b"
)
TRIGGER_CHANGE_CLASSES = {
    "candidate-identity-artifact": ["identity-artifact-change"],
    "candidate-package-byte": ["package-byte-change"],
    "contribution-decision": ["disposition-only-change"],
    "installed-membership-or-identity": ["installed-library-evidence-change"],
    "instruction-discovery-route": ["installed-library-evidence-change"],
    "source-revision-tree-content-or-license": ["source-evidence-change"],
}


def change_class_output_selectors(change_class: str) -> list[dict[str, str]]:
    contract = REFRESH_CONTRACT.as_posix()
    return [
        {
            "path": contract,
            "selector": f"/regeneration/change_classes/{change_class}/required_steps",
        },
        {
            "path": contract,
            "selector": "/regeneration/affected_distribution_derivation/distribution_closure",
        },
        {
            "path": contract,
            "selector": "/regeneration/distribution_identity_artifacts",
        },
        {
            "path": contract,
            "selector": "/regeneration/receipt_artifact_bindings",
        },
        {"path": contract, "selector": "/regeneration/required_outputs"},
    ]


RECEIPT_BINDINGS = [
    {
        "binding_id": "candidate-identity",
        "digest_contract": "canonical-json-sha256",
        "evidence_role": "final-refresh-input",
        "path": SOURCE_MANIFEST.as_posix(),
        "selector": "/candidate",
    },
    {
        "binding_id": "contribution-ledger",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "current-and-final-refresh-input",
        "path": CONTRIBUTION_LEDGER.as_posix(),
        "selector": "/",
    },
    {
        "binding_id": "disposition-ledger",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "current-and-final-refresh-input",
        "path": DISPOSITION_LEDGER.as_posix(),
        "selector": "/",
    },
    {
        "binding_id": "final-installed-library-rescout-schema",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "current-and-final-refresh-input",
        "path": FINAL_RESCOUT_SCHEMA.as_posix(),
        "selector": "/",
    },
    {
        "binding_id": "final-installed-library-rescout-v1",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "required-at-final-refresh",
        "path": FINAL_RESCOUT_ARTIFACT["path"],
        "selector": "/",
    },
    {
        "binding_id": "installed-host-initial-personal-cachyos-v1",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "historical-baseline",
        "path": "release/source-skill-lineage/installed-hosts/initial-personal-cachyos-v1.json",
        "selector": "/",
    },
    {
        "binding_id": "installed-host-initial-work-macos-v1",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "historical-baseline",
        "path": "release/source-skill-lineage/installed-hosts/initial-work-macos-v1.json",
        "selector": "/",
    },
    {
        "binding_id": "release-refresh-contract",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "current-and-final-refresh-input",
        "path": REFRESH_CONTRACT.as_posix(),
        "selector": "/",
    },
    {
        "binding_id": "source-manifest",
        "digest_contract": "raw-byte-sha256",
        "evidence_role": "current-and-final-refresh-input",
        "path": SOURCE_MANIFEST.as_posix(),
        "selector": "/",
    },
]
DISTRIBUTION_REGENERATION = [
    {
        "conditional_regenerate_paths": [],
        "id": "artifact-customs",
        "regenerate_paths": ["release/plugin-content-locks/artifact-customs.json"],
    },
    {
        "conditional_regenerate_paths": [],
        "id": "mergecraft",
        "regenerate_paths": ["release/plugin-content-locks/mergecraft.json"],
    },
    {
        "conditional_regenerate_paths": [],
        "id": "rolecasting",
        "regenerate_paths": ["plugins/rolecasting/content-lock.json"],
    },
    {
        "conditional_regenerate_paths": [
            {
                "condition": "owning-set-or-count-change",
                "path": "release/task-witness/tw4-suite-inventory.json",
            }
        ],
        "id": "task-witness",
        "regenerate_paths": ["release/task-witness/source-shape-review.json"],
    },
    {
        "conditional_regenerate_paths": [],
        "id": "tricritical",
        "regenerate_paths": ["plugins/tricritical/content-lock.json"],
    },
    {
        "conditional_regenerate_paths": [],
        "id": "versionkeeping",
        "regenerate_paths": ["release/plugin-content-locks/versionkeeping.json"],
    },
]
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class DispositionError(ValueError):
    pass


def require(condition: bool, diagnostic: str) -> None:
    if not condition:
        raise DispositionError(diagnostic)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def content_sha256(value: dict[str, object]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    return "sha256:" + hashlib.sha256(canonical_bytes(unsigned)).hexdigest()


def raw_sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_document(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def strict_json(raw: bytes, label: str) -> dict[str, object]:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            require(key not in value, f"duplicate JSON key in {label}")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda _value: require(
                False, f"non-finite JSON value in {label}"
            ),
        )
    except DispositionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise DispositionError(f"{label} is not valid JSON") from None
    require(type(value) is dict, f"{label} must be an object")
    return value


def read_document(repository: Path, relative: Path, label: str) -> tuple[dict, bytes]:
    target = repository / relative
    try:
        require(target.is_file() and not target.is_symlink(), f"{label} is missing")
        raw = target.read_bytes()
    except OSError:
        raise DispositionError(f"{label} is missing") from None
    value = strict_json(raw, label)
    require(raw == canonical_document(value), f"{label} is not canonical JSON")
    return value, raw


def read_json_object(repository: Path, relative: str, label: str) -> dict[str, object]:
    target = repository / relative
    try:
        require(target.is_file() and not target.is_symlink(), f"{label} is missing")
        raw = target.read_bytes()
    except OSError:
        raise DispositionError(f"{label} is missing") from None
    return strict_json(raw, label)


def utc_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise DispositionError(
            "final installed-library rescout timestamp drift"
        ) from None


def validate_final_rescout_artifact(
    value: dict[str, object],
    *,
    repository: Path,
    schema: dict[str, object],
    source_manifest: dict[str, object],
    source_raw: bytes,
    contribution_raw: bytes,
    disposition_raw: bytes,
    refresh_raw: bytes,
) -> None:
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ModuleNotFoundError:
        raise DispositionError(
            "jsonschema is required to validate the final installed-library rescout artifact"
        ) from None

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise DispositionError(
            "final installed-library rescout schema contract drift"
        ) from None
    require(
        not list(Draft202012Validator(schema).iter_errors(value)),
        "final installed-library rescout schema mismatch",
    )

    started_at = utc_timestamp(value["started_at_utc"])
    completed_at = utc_timestamp(value["completed_at_utc"])
    require(
        started_at <= completed_at,
        "final installed-library rescout timestamp drift",
    )
    profile_manifests = value["profile_manifests"]
    for manifest in profile_manifests:
        observed_at = utc_timestamp(manifest["observed_at_utc"])
        require(
            started_at <= observed_at <= completed_at,
            "final installed-library rescout timestamp drift",
        )

    profile_ids = [manifest["profile_id"] for manifest in profile_manifests]
    enumerated_profile_ids = value["enumerated_profile_ids"]
    require(
        profile_ids == sorted(set(profile_ids))
        and enumerated_profile_ids == profile_ids,
        "final installed-library rescout profile inventory drift",
    )

    inventory = value["instruction_inventory"]
    entries = inventory["entries"]
    entry_keys = [(entry["profile_id"], entry["route_id"]) for entry in entries]
    require(
        entry_keys == sorted(set(entry_keys)),
        "final installed-library rescout instruction inventory drift",
    )
    require(
        enumerated_profile_ids
        == sorted({entry["profile_id"] for entry in entries}),
        "final installed-library rescout profile inventory drift",
    )

    observed_counts = {surface: 0 for surface in RESCOUT_SURFACES}
    for entry in entries:
        observed_counts[entry["surface"]] += 1
    require(
        inventory["surface_counts"] == observed_counts,
        "final installed-library rescout surface count drift",
    )
    require(
        inventory["sha256"] == raw_sha256(canonical_bytes(entries)),
        "final installed-library rescout inventory digest mismatch",
    )
    require(
        value["content_sha256"] == content_sha256(value),
        "final installed-library rescout content digest mismatch",
    )

    require(
        value["candidate_identity_sha256"]
        == raw_sha256(canonical_bytes(source_manifest["candidate"])),
        "final installed-library rescout candidate binding mismatch",
    )
    require(
        value["source_manifest_sha256"] == raw_sha256(source_raw)
        and value["contribution_ledger_sha256"] == raw_sha256(contribution_raw)
        and value["disposition_ledger_sha256"] == raw_sha256(disposition_raw)
        and value["release_refresh_contract_sha256"] == raw_sha256(refresh_raw),
        "final installed-library rescout input binding mismatch",
    )

    for manifest in profile_manifests:
        bound_manifest, bound_raw = read_document(
            repository,
            Path(manifest["path"]),
            "final installed-library rescout profile manifest",
        )
        require(
            manifest["sha256"] == raw_sha256(bound_raw)
            and bound_manifest.get("profile_id") == manifest["profile_id"]
            and bound_manifest.get("observed_at_utc") == manifest["observed_at_utc"],
            "final installed-library rescout profile manifest binding mismatch",
        )


def validate_content_document(
    value: dict[str, object], expected_fields: set[str], label: str
) -> None:
    require(set(value) == expected_fields, f"{label} schema drift")
    digest = value.get("content_sha256")
    require(
        type(digest) is str
        and SHA256.fullmatch(digest) is not None
        and digest == content_sha256(value),
        f"{label} content digest mismatch",
    )


def nonempty_string(value: object, diagnostic: str) -> str:
    require(type(value) is str and bool(value.strip()), diagnostic)
    return value


def string_list(value: object, diagnostic: str) -> list[str]:
    require(
        type(value) is list
        and bool(value)
        and all(type(item) is str and bool(item.strip()) for item in value),
        diagnostic,
    )
    require(value == sorted(set(value)), diagnostic)
    return value


def issue_list(value: object, diagnostic: str) -> list[int]:
    require(
        type(value) is list
        and all(type(item) is int and item > 0 for item in value)
        and value == sorted(set(value)),
        diagnostic,
    )
    return value


def safe_evidence_paths(value: object, *, repository: Path) -> list[str]:
    paths = string_list(value, "disposition evidence paths drift")
    for relative in paths:
        normalized = posixpath.normpath(relative)
        require(
            not relative.startswith(("/", "~"))
            and "\\" not in relative
            and normalized == relative
            and normalized not in {".", ".."}
            and not normalized.startswith("../")
            and not any(
                marker in relative
                for marker in ("/Users/", "/home/", "file://", "$HOME")
            ),
            "disposition ledger contains a private or absolute path",
        )
        target = repository / relative
        require(
            target.is_file() and not target.is_symlink(),
            "disposition evidence path is missing",
        )
    return paths


def validate_skill_dispositions(
    value: object, *, source_id: str, expected_skill_ids: list[str]
) -> None:
    require(type(value) is list, "skill dispositions must be a list")
    observed: list[str] = []
    for record in value:
        require(
            type(record) is dict
            and set(record)
            == {"disposition", "durable_owners", "rationale", "skill_ids"},
            "skill disposition schema drift",
        )
        disposition = record["disposition"]
        require(
            type(disposition) is str and disposition in ALLOWED_DISPOSITIONS,
            "skill disposition value drift",
        )
        string_list(record["durable_owners"], "skill disposition owner drift")
        nonempty_string(record["rationale"], "skill disposition rationale drift")
        skill_ids = string_list(record["skill_ids"], "skill disposition ids drift")
        observed.extend(skill_ids)

    if source_id in {"matt-pocock-skill-set", "superpowers-skill-set"}:
        require(
            observed == sorted(set(observed))
            and observed == expected_skill_ids,
            f"{source_id} skill disposition coverage drift",
        )
        observed_dispositions = {
            skill_id: record["disposition"]
            for record in value
            for skill_id in record["skill_ids"]
        }
        if source_id == "matt-pocock-skill-set":
            require(
                observed_dispositions.get("resolving-merge-conflicts")
                == "absorb-or-refresh"
                and all(
                    disposition == "retain-side-by-side"
                    for skill_id, disposition in observed_dispositions.items()
                    if skill_id != "resolving-merge-conflicts"
                ),
                "matt-pocock-skill-set skill disposition value drift",
            )
            code_review = next(
                (
                    record
                    for record in value
                    if record["skill_ids"] == ["code-review"]
                ),
                None,
            )
            require(
                type(code_review) is dict
                and code_review["disposition"] == "retain-side-by-side"
                and code_review["durable_owners"]
                == ["matt-pocock:code-review", "tricritical"],
                "matt-pocock code-review relationship drift",
            )
        else:
            require(
                all(
                    disposition == "supersede-and-remove"
                    for disposition in observed_dispositions.values()
                ),
                "superpowers-skill-set skill disposition value drift",
            )
    elif source_id == "review-atlas-private":
        require(
            observed == ["private-overlay", "public-core"],
            "review-atlas component disposition drift",
        )
        components = {
            skill_id: record
            for record in value
            for skill_id in record["skill_ids"]
        }
        require(
            components["private-overlay"]["disposition"] == "retain-side-by-side"
            and components["private-overlay"]["durable_owners"]
            == ["review-atlas-private-overlay"]
            and components["public-core"]["disposition"] == "absorb-or-refresh"
            and components["public-core"]["durable_owners"]
            == ["mergecraft:writing-reviewable-pr-descriptions"],
            "review-atlas component disposition drift",
        )
    else:
        require(not value, "unexpected aggregate skill dispositions")


def validate_disposition_ledger(
    value: dict[str, object],
    *,
    repository: Path,
    source_manifest: dict[str, object],
    contribution_ledger: dict[str, object],
    source_raw: bytes,
    contribution_raw: bytes,
) -> None:
    validate_content_document(
        value,
        {
            "authority",
            "content_sha256",
            "contract",
            "dispositions",
            "evidence_bindings",
            "limitations",
            "schema_version",
        },
        "disposition ledger",
    )
    require(value["schema_version"] == 1, "disposition ledger schema version drift")
    require(
        value["contract"] == "coordinated-source-skill-disposition-ledger-v1",
        "disposition ledger contract drift",
    )
    require(value["authority"] == AUTHORITY, "source disposition authority drift")
    require(
        value["limitations"] == LEDGER_LIMITATIONS,
        "disposition limitations drift",
    )

    bindings = value["evidence_bindings"]
    require(
        type(bindings) is dict
        and set(bindings) == {"contribution_ledger", "source_manifest"},
        "disposition evidence binding schema drift",
    )
    expected_bindings = {
        "contribution_ledger": {
            "path": CONTRIBUTION_LEDGER.as_posix(),
            "sha256": "sha256:" + hashlib.sha256(contribution_raw).hexdigest(),
        },
        "source_manifest": {
            "path": SOURCE_MANIFEST.as_posix(),
            "sha256": "sha256:" + hashlib.sha256(source_raw).hexdigest(),
        },
    }
    require(
        bindings.get("contribution_ledger") == expected_bindings["contribution_ledger"],
        "contribution ledger evidence binding drift",
    )
    require(
        bindings.get("source_manifest") == expected_bindings["source_manifest"],
        "source manifest evidence binding drift",
    )

    sources_value = source_manifest.get("sources")
    contributions_value = contribution_ledger.get("contributions")
    require(type(sources_value) is list, "source evidence inventory drift")
    require(
        type(contributions_value) is list,
        "contribution evidence inventory drift",
    )
    sources = {
        item.get("id"): item
        for item in sources_value
        if type(item) is dict and type(item.get("id")) is str
    }
    contributions = {
        item.get("id"): item
        for item in contributions_value
        if type(item) is dict and type(item.get("id")) is str
    }
    require(len(sources) == len(sources_value), "source evidence inventory drift")
    require(
        len(contributions) == len(contributions_value),
        "contribution evidence inventory drift",
    )
    require(
        all("disposition" not in item for item in contributions_value),
        "issue 49 contribution evidence acquired a disposition",
    )
    require(
        contribution_ledger.get("source_manifest")
        == {
            "path": SOURCE_MANIFEST.as_posix(),
            "sha256": "sha256:" + hashlib.sha256(source_raw).hexdigest(),
        },
        "issue 49 source evidence binding drift",
    )

    dispositions = value["dispositions"]
    require(type(dispositions) is list, "contribution dispositions must be a list")
    disposition_ids = [
        item.get("contribution_id") if type(item) is dict else None
        for item in dispositions
    ]
    require(
        all(
            type(identifier) is str and bool(identifier)
            for identifier in disposition_ids
        ),
        "contribution disposition id drift",
    )
    require(
        disposition_ids == sorted(set(disposition_ids)),
        "contribution dispositions must be sorted and unique",
    )
    require(
        set(disposition_ids) == set(contributions),
        "contribution disposition coverage drift",
    )

    for record in dispositions:
        require(
            type(record) is dict
            and set(record)
            == {
                "authority",
                "contribution_id",
                "disposition",
                "durable_owners",
                "evidence_paths",
                "follow_up_issues",
                "rationale",
                "skill_dispositions",
                "source_id",
            },
            "contribution disposition schema drift",
        )
        contribution = contributions[record["contribution_id"]]
        require(
            record["source_id"] == contribution.get("source_id")
            and record["source_id"] in sources,
            "contribution disposition source drift",
        )
        disposition = record["disposition"]
        require(
            type(disposition) is str and disposition in ALLOWED_DISPOSITIONS,
            "contribution disposition value drift",
        )
        require(
            record["authority"] == AUTHORITY,
            "source disposition grants host removal authority",
        )
        string_list(record["durable_owners"], "contribution durable owner drift")
        safe_evidence_paths(record["evidence_paths"], repository=repository)
        follow_ups = issue_list(
            record["follow_up_issues"], "follow-up issue list drift"
        )
        nonempty_string(record["rationale"], "contribution rationale drift")
        if disposition.startswith("defer-"):
            require(bool(follow_ups), "deferred disposition lacks a follow-up issue")
        if disposition == "supersede-and-remove":
            require(51 in follow_ups, "superseded source lacks convergence ownership")
        if record["contribution_id"] == "cursor-thermos-rule-lineage":
            require(
                disposition == "defer-blocking"
                and follow_ups == [56]
                and record["evidence_paths"]
                == [
                    "plugins/tricritical/NOTICE",
                    "plugins/tricritical/skills/review/SKILL.md",
                ],
                "Thermos rule-level mapping disposition drift",
            )
        if record["contribution_id"] == "review-atlas-lineage":
            require(
                disposition == "split-by-component",
                "review-atlas component disposition drift",
            )
        source = sources[record["source_id"]]
        validate_skill_dispositions(
            record["skill_dispositions"],
            source_id=record["source_id"],
            expected_skill_ids=source.get("skill_ids", []),
        )

    observed_inventory_sha256 = (
        "sha256:" + hashlib.sha256(canonical_bytes(dispositions)).hexdigest()
    )
    require(
        observed_inventory_sha256 == DISPOSITION_INVENTORY_SHA256,
        "disposition inventory drift",
    )


def validate_refresh_contract(
    value: dict[str, object],
    *,
    repository: Path,
    source_manifest: dict[str, object],
    source_raw: bytes,
    contribution_raw: bytes,
    disposition_raw: bytes,
    refresh_raw: bytes,
    final_rescout_schema: dict[str, object],
    final_rescout_schema_raw: bytes,
    final_rescout: dict[str, object] | None,
) -> None:
    validate_content_document(
        value,
        {
            "authority",
            "candidate_identity",
            "content_sha256",
            "contract",
            "disposition_ledger",
            "final_rescout_artifact",
            "follow_up_issues",
            "freshness",
            "installed_library_rescout",
            "invalidation",
            "regeneration",
            "schema_version",
            "workflow",
        },
        "release refresh contract",
    )
    require(value["schema_version"] == 1, "release refresh schema version drift")
    require(
        value["contract"] == "coordinated-source-skill-release-refresh-v1",
        "release refresh contract drift",
    )
    require(
        value["authority"]
        == {
            "host_mutation": "not-authorized",
            "release_eligibility": "not-asserted",
            "security_sensitive_receipt_integration": "deferred-to-daybreak-follow-up",
        },
        "release refresh authority drift",
    )
    require(
        value["disposition_ledger"]
        == {
            "path": DISPOSITION_LEDGER.as_posix(),
            "sha256": "sha256:" + hashlib.sha256(disposition_raw).hexdigest(),
        },
        "release refresh disposition binding drift",
    )
    require(
        value["final_rescout_artifact"] == FINAL_RESCOUT_ARTIFACT,
        "final installed-library rescout artifact contract drift",
    )
    require(
        FINAL_RESCOUT_ARTIFACT["schema"]["sha256"]
        == "sha256:" + hashlib.sha256(final_rescout_schema_raw).hexdigest(),
        "final installed-library rescout schema binding drift",
    )
    require(
        set(final_rescout_schema)
        == {
            "$defs",
            "$id",
            "$schema",
            "additionalProperties",
            "properties",
            "required",
            "title",
            "type",
            "x-invariants",
        }
        and final_rescout_schema["$schema"]
        == "https://json-schema.org/draft/2020-12/schema"
        and final_rescout_schema["type"] == "object"
        and final_rescout_schema["additionalProperties"] is False
        and type(final_rescout_schema["required"]) is list
        and set(final_rescout_schema["required"])
        == set(final_rescout_schema["properties"])
        and final_rescout_schema["x-invariants"] == FINAL_RESCOUT_INVARIANTS,
        "final installed-library rescout schema contract drift",
    )
    if final_rescout is not None:
        validate_final_rescout_artifact(
            final_rescout,
            repository=repository,
            schema=final_rescout_schema,
            source_manifest=source_manifest,
            source_raw=source_raw,
            contribution_raw=contribution_raw,
            disposition_raw=disposition_raw,
            refresh_raw=refresh_raw,
        )

    follow_ups = value["follow_up_issues"]
    require(
        follow_ups
        == [
            {
                "issue": 51,
                "owns": "install-before-remove host convergence, duplicate and shadow checks, rollback, and proof that retired routes are absent",
            },
            {
                "issue": 56,
                "owns": "Daybreak review, Thermos rule-level mapping, and security-sensitive integration of disposition evidence with hardened receipts and the public-release gate",
            },
        ],
        "release refresh follow-up ownership drift",
    )

    candidate = value["candidate_identity"]
    require(
        type(candidate) is dict
        and set(candidate)
        == {
            "distributions",
            "package_tuple_fields",
            "repository_tuple_fields",
            "source",
        },
        "candidate identity contract drift",
    )
    require(
        candidate["source"]
        == {"json_pointer": "/candidate", "path": SOURCE_MANIFEST.as_posix()}
        and candidate["repository_tuple_fields"] == CANDIDATE_REPOSITORY_TUPLE
        and candidate["package_tuple_fields"] == CANDIDATE_PACKAGE_TUPLE,
        "candidate identity contract drift",
    )
    require(
        candidate["distributions"] == DISTRIBUTION_IDENTITIES,
        "candidate identity distribution contract drift",
    )
    for distribution in DISTRIBUTION_IDENTITIES:
        for relative in distribution["identity_artifact_paths"]:
            target = repository / relative
            require(
                target.is_file() and not target.is_symlink(),
                "candidate identity artifact path is missing",
            )

    freshness = value["freshness"]
    require(
        type(freshness) is dict
        and set(freshness)
        == {"maximum_age_seconds", "measured_from", "measured_to"},
        "release refresh freshness schema drift",
    )
    require(
        freshness["maximum_age_seconds"] == 86400,
        "release refresh maximum age drift",
    )
    require(
        freshness["measured_from"] == FRESHNESS["measured_from"],
        "release refresh measured_from boundary drift",
    )
    require(
        freshness["measured_to"] == FRESHNESS["measured_to"],
        "release refresh measured_to boundary drift",
    )

    rescout = value["installed_library_rescout"]
    require(
        type(rescout) is dict
        and set(rescout) == {"dispositions", "source_sync", "surfaces"},
        "installed library rescout schema drift",
    )
    require(
        rescout["surfaces"] == RESCOUT_SURFACES,
        "installed instruction library rescout scope drift",
    )
    require(
        rescout["dispositions"] == RESCOUT_DISPOSITIONS,
        "installed instruction library disposition set drift",
    )
    require(
        rescout["source_sync"]
        == "three-way-semantic-reconciliation-of-historical-import-current-source-and-current-plugin",
        "source synchronization workflow drift",
    )

    invalidation = value["invalidation"]
    require(
        type(invalidation) is dict
        and set(invalidation) == {"immediate_triggers", "requalification_scope", "roots"},
        "release refresh invalidation schema drift",
    )
    require(
        invalidation["immediate_triggers"] == INVALIDATION_TRIGGERS,
        "release refresh invalidation trigger drift",
    )
    require(
        invalidation["requalification_scope"] == "affected-dependency-closure"
        and invalidation["roots"]
        == [
            {"path": "evals/control-plane-matrix.json", "selector": "/skills"},
            {
                "path": REFRESH_CONTRACT.as_posix(),
                "selector": "/regeneration/affected_distribution_derivation/distribution_closure",
            },
        ],
        "release refresh dependency closure drift",
    )

    regeneration = value["regeneration"]
    require(
        type(regeneration) is dict
        and set(regeneration)
        == {
            "affected_distribution_derivation",
            "change_classes",
            "distribution_identity_artifacts",
            "receipt_artifact_bindings",
            "required_outputs",
            "trigger_to_change_classes",
        },
        "release refresh regeneration schema drift",
    )
    derivation = regeneration["affected_distribution_derivation"]
    require(
        type(derivation) is dict
        and set(derivation)
        == {
            "all_distribution_ids",
            "common_dependency_paths",
            "dependency_graph",
            "distribution_closure",
            "method",
        }
        and derivation["all_distribution_ids"]
        == [item["id"] for item in DISTRIBUTION_IDENTITIES]
        and derivation["common_dependency_paths"] == COMMON_DEPENDENCY_PATHS
        and derivation["method"] == "transitive-affected-dependency-closure",
        "release refresh affected-distribution derivation drift",
    )
    graph = derivation["dependency_graph"]
    require(
        type(graph) is dict
        and set(graph) == {"edge_semantics", "edges", "transitive_closures"}
        and graph["edge_semantics"] == DEPENDENCY_EDGE_SEMANTICS
        and graph["edges"] == DEPENDENCY_EDGES,
        "release refresh dependency graph drift",
    )
    distribution_ids = [item["id"] for item in DISTRIBUTION_IDENTITIES]
    derived_closures: dict[str, list[str]] = {}
    for seed in distribution_ids:
        affected = {seed}
        changed = True
        while changed:
            changed = False
            for edge in DEPENDENCY_EDGES:
                if edge["provider"] in affected and edge["consumer"] not in affected:
                    affected.add(edge["consumer"])
                    changed = True
        derived_closures[seed] = sorted(affected)
    require(
        graph["transitive_closures"] == derived_closures,
        "release refresh transitive dependency closure drift",
    )

    artifact_topology = read_json_object(
        repository,
        "plugins/artifact-customs/topology.json",
        "Artifact Customs topology",
    )
    require(
        set(artifact_topology.get("outward_plugins", []))
        == {"mergecraft", "rolecasting", "tricritical", "versionkeeping"},
        "Artifact Customs dependency evidence drift",
    )
    mergecraft_topology = read_json_object(
        repository, "plugins/mergecraft/topology.json", "Mergecraft topology"
    )
    operations = mergecraft_topology.get("operations")
    require(type(operations) is list, "Mergecraft dependency evidence drift")
    import_providers = {
        operation["import"].split(":", 1)[0]
        for operation in operations
        if type(operation) is dict and type(operation.get("import")) is str
    }
    skills = mergecraft_topology.get("skills")
    require(type(skills) is list, "Mergecraft dependency evidence drift")
    task_witness_profile_required = any(
        type(skill) is dict
        and skill.get("name") == "publishing-reviewable-prs"
        and type(skill.get("contract")) is dict
        and "authenticated-task-witness-process-profile"
        in skill["contract"].get("inputs", [])
        for skill in skills
    )
    require(
        import_providers == {"tricritical", "versionkeeping"}
        and task_witness_profile_required,
        "Mergecraft dependency evidence drift",
    )
    tricritical_topology = read_json_object(
        repository, "plugins/tricritical/topology.json", "Tricritical topology"
    )
    tricritical_skills = tricritical_topology.get("skills")
    require(
        type(tricritical_skills) is dict
        and type(tricritical_skills.get("review")) is dict
        and "adapter:rolecasting-invocation-topology-receipt"
        in tricritical_skills["review"].get("requires", []),
        "Tricritical dependency evidence drift",
    )
    closure = derivation["distribution_closure"]
    require(
        type(closure) is list
        and [
            item.get("id") if type(item) is dict else None
            for item in closure
        ]
        == [item["id"] for item in DISTRIBUTION_IDENTITIES],
        "release refresh distribution closure drift",
    )
    for distribution, identity in zip(closure, DISTRIBUTION_IDENTITIES, strict=True):
        require(
            set(distribution)
            == {
                "documentation_paths",
                "evaluation_paths",
                "id",
                "identity_artifact_paths",
                "manifest_path",
                "package_root",
                "test_paths",
                "topology_and_owner_paths",
                "validation_entrypoints",
            }
            and distribution["id"] == identity["id"]
            and distribution["package_root"] == identity["plugin_root"]
            and distribution["manifest_path"]
            == f'{identity["plugin_root"]}/plugin.json'
            and distribution["identity_artifact_paths"]
            == identity["identity_artifact_paths"],
            "release refresh distribution closure drift",
        )
        path_fields = [
            "documentation_paths",
            "evaluation_paths",
            "identity_artifact_paths",
            "test_paths",
            "topology_and_owner_paths",
            "validation_entrypoints",
        ]
        paths = [distribution["manifest_path"], distribution["package_root"]]
        for field in path_fields:
            paths.extend(string_list(distribution[field], "distribution closure path drift"))
        for relative in paths:
            target = repository / relative
            require(
                target.exists() and not target.is_symlink(),
                "distribution closure path is missing",
            )
    for relative in COMMON_DEPENDENCY_PATHS:
        target = repository / relative
        require(
            target.is_file() and not target.is_symlink(),
            "common dependency path is missing",
        )
    require(
        "sha256:" + hashlib.sha256(canonical_bytes(closure)).hexdigest()
        == DISTRIBUTION_CLOSURE_SHA256,
        "release refresh distribution closure inventory drift",
    )
    change_classes = regeneration["change_classes"]
    require(
        type(change_classes) is dict
        and all(
            type(change_class) is dict
            and type(change_class.get("required_steps")) is list
            and "regenerate-versioned-final-installed-library-rescout-artifact"
            in change_class["required_steps"]
            for change_class in change_classes.values()
        ),
        "final installed-library rescout regeneration dependency drift",
    )
    require(
        all(
            type(change_class) is dict
            and change_class.get("output_selectors")
            == change_class_output_selectors(name)
            for name, change_class in change_classes.items()
        ),
        "release refresh change-class output selector drift",
    )
    require(
        change_classes
        == {
            "disposition-only-change": {
                "changes_package_bytes": False,
                "output_selectors": change_class_output_selectors(
                    "disposition-only-change"
                ),
                "required_steps": DISPOSITION_ONLY_STEPS,
            },
            "package-byte-change": {
                "changes_package_bytes": True,
                "output_selectors": change_class_output_selectors(
                    "package-byte-change"
                ),
                "required_steps": PACKAGE_BYTE_STEPS,
            },
            "identity-artifact-change": {
                "changes_package_bytes": False,
                "output_selectors": change_class_output_selectors(
                    "identity-artifact-change"
                ),
                "required_steps": IDENTITY_ARTIFACT_STEPS,
            },
            "installed-library-evidence-change": {
                "changes_package_bytes": False,
                "output_selectors": change_class_output_selectors(
                    "installed-library-evidence-change"
                ),
                "required_steps": INSTALLED_LIBRARY_EVIDENCE_STEPS,
            },
            "source-evidence-change": {
                "changes_package_bytes": False,
                "output_selectors": change_class_output_selectors(
                    "source-evidence-change"
                ),
                "required_steps": SOURCE_EVIDENCE_STEPS,
            },
        },
        "release refresh change-class fanout drift",
    )
    require(
        regeneration["distribution_identity_artifacts"]
        == DISTRIBUTION_REGENERATION,
        "release refresh distribution artifact fanout drift",
    )
    require(
        regeneration["receipt_artifact_bindings"] == RECEIPT_BINDINGS,
        "release refresh receipt binding drift",
    )
    require(
        regeneration["required_outputs"] == REGENERATION_OUTPUTS,
        "release refresh regeneration output drift",
    )
    require(
        regeneration["trigger_to_change_classes"] == TRIGGER_CHANGE_CLASSES
        and set(regeneration["trigger_to_change_classes"])
        == set(INVALIDATION_TRIGGERS),
        "release refresh invalidation trigger fanout drift",
    )
    for binding in RECEIPT_BINDINGS:
        if binding["binding_id"] == "final-installed-library-rescout-v1":
            continue
        target = repository / binding["path"]
        require(
            target.is_file() and not target.is_symlink(),
            "release refresh receipt artifact path is missing",
        )

    workflow = value["workflow"]
    require(
        type(workflow) is dict
        and set(workflow)
        == {
            "convergence_owner_issue",
            "final_refresh_owner_issue",
            "ordered_steps",
            "source_disposition_owner_issue",
        },
        "release refresh workflow schema drift",
    )
    require(
        workflow["final_refresh_owner_issue"] == 45,
        "final release refresh owner drift",
    )
    require(
        workflow["source_disposition_owner_issue"] == 50
        and workflow["convergence_owner_issue"] == 51,
        "release workflow issue ownership drift",
    )
    require(workflow["ordered_steps"] == WORKFLOW_STEPS, "release refresh step drift")


def validate(repository: Path, *, require_final_rescout: bool = False) -> None:
    source_manifest, source_raw = read_document(
        repository, SOURCE_MANIFEST, "source manifest"
    )
    contribution_ledger, contribution_raw = read_document(
        repository, CONTRIBUTION_LEDGER, "contribution ledger"
    )
    disposition_ledger, disposition_raw = read_document(
        repository, DISPOSITION_LEDGER, "disposition ledger"
    )
    refresh_contract, refresh_raw = read_document(
        repository, REFRESH_CONTRACT, "release refresh contract"
    )
    final_rescout_schema, final_rescout_schema_raw = read_document(
        repository,
        FINAL_RESCOUT_SCHEMA,
        "final installed-library rescout schema",
    )
    final_rescout_path = repository / FINAL_RESCOUT_ARTIFACT["path"]
    final_rescout = None
    if final_rescout_path.exists() or final_rescout_path.is_symlink():
        final_rescout, _ = read_document(
            repository,
            Path(FINAL_RESCOUT_ARTIFACT["path"]),
            "final installed-library rescout artifact",
        )
    require(
        not require_final_rescout or final_rescout is not None,
        "final installed-library rescout artifact is required",
    )
    validate_disposition_ledger(
        disposition_ledger,
        repository=repository,
        source_manifest=source_manifest,
        contribution_ledger=contribution_ledger,
        source_raw=source_raw,
        contribution_raw=contribution_raw,
    )
    validate_refresh_contract(
        refresh_contract,
        repository=repository,
        source_manifest=source_manifest,
        source_raw=source_raw,
        contribution_raw=contribution_raw,
        disposition_raw=disposition_raw,
        refresh_raw=refresh_raw,
        final_rescout_schema=final_rescout_schema,
        final_rescout_schema_raw=final_rescout_schema_raw,
        final_rescout=final_rescout,
    )


def main(argv: list[str]) -> int:
    arguments = argv[1:]
    require_final_rescout = bool(
        arguments and arguments[0] == "--require-final-rescout"
    )
    if require_final_rescout:
        arguments = arguments[1:]
    if len(arguments) > 1 or (arguments and arguments[0].startswith("-")):
        print(
            "usage: validate_source_skill_disposition.py [--require-final-rescout] [repository]",
            file=sys.stderr,
        )
        return 2
    repository = Path(arguments[0] if arguments else ".").resolve()
    try:
        validate(repository, require_final_rescout=require_final_rescout)
    except DispositionError as error:
        print(f"source-skill-disposition: {error}", file=sys.stderr)
        return 1
    print("source-skill-disposition-valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
