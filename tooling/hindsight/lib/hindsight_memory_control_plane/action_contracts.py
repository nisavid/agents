"""Single closed catalog for controller action validation and dispatch."""

from __future__ import annotations

from types import MappingProxyType


MUTATION_ACTION_KINDS = frozenset(
    {"import_bank", "migrate_bank", "replace_canonical_bank"}
)
DESTRUCTIVE_ACTION_KINDS = MUTATION_ACTION_KINDS

ACTION_SCHEMAS = MappingProxyType({
    "activate_model": frozenset(
        {"profile_id", "provider_id", "model_id", "revision", "artifact_digest"}
    ),
    "configure_bank": frozenset({"bank", "artifact_digest"}),
    "configure_profile": frozenset({"profile_id", "artifact_digest"}),
    "create_bank": frozenset({"bank"}),
    "install_model": frozenset(
        {"profile_id", "provider_id", "model_id", "revision", "artifact_digest"}
    ),
    **{
        kind: frozenset(
            {
                "artifact_digest",
                "archive_digest",
                "restore_evidence_digest",
                "source_bank",
                "target_bank",
            }
        )
        for kind in MUTATION_ACTION_KINDS
    },
    "reload_profile": frozenset({"profile_id", "reason_code"}),
    "report_unmanaged": frozenset({"profile_id", "reason_code"}),
    "set_auto_consolidation": frozenset({"bank", "enabled"}),
    "set_memory_defense": frozenset({"bank", "enabled"}),
    "upsert_directive": frozenset({"bank", "directive_id", "artifact_digest"}),
    "upsert_model": frozenset({"bank", "model_id", "revision", "artifact_digest"}),
})

# Adapter method names are data so fake and HTTP adapters cannot silently drift.
ACTION_METHODS = MappingProxyType({
    "activate_model": "upsert_model",
    "configure_bank": "patch_config",
    "configure_profile": "patch_config",
    "install_model": "upsert_model",
    "set_auto_consolidation": "patch_config",
    "set_memory_defense": "patch_config",
    "upsert_directive": "upsert_directive",
    "upsert_model": "upsert_model",
})

DIRECT_ACTION_KINDS = frozenset(
    {"create_bank", "import_bank", "migrate_bank", "reload_profile", "replace_canonical_bank", "report_unmanaged"}
)

ARTIFACT_ACTION_KINDS = frozenset(
    kind for kind, schema in ACTION_SCHEMAS.items()
    if "artifact_digest" in schema
)

EXECUTABLE_ACTION_KINDS = frozenset(ACTION_METHODS) | DIRECT_ACTION_KINDS

if frozenset(ACTION_METHODS) & DIRECT_ACTION_KINDS:
    raise RuntimeError("action kind has multiple adapter routes")
if frozenset(ACTION_SCHEMAS) - EXECUTABLE_ACTION_KINDS:
    raise RuntimeError("plannable action catalog contains no adapter route")
if EXECUTABLE_ACTION_KINDS - frozenset(ACTION_SCHEMAS):
    raise RuntimeError("executable action catalog contains no validation schema")
if MUTATION_ACTION_KINDS - EXECUTABLE_ACTION_KINDS:
    raise RuntimeError("migration action catalog contains no adapter route")
