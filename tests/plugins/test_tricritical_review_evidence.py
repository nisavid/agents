from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from typing import Any, ClassVar

from tests.plugins import test_rolecasting_dispatch_evidence as rolecasting_evidence

REPOSITORY = Path(__file__).resolve().parents[2]
PROVIDER = REPOSITORY / "plugins" / "tricritical" / "task-witness-provider.json"
VALIDATOR = (
    REPOSITORY
    / "plugins"
    / "tricritical"
    / "skills"
    / "loop"
    / "scripts"
    / "review_evidence.py"
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def raw_document(value: object) -> bytes:
    return canonical(value) + b"\n"


def sha(value: bytes | str) -> str:
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def signed(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "content_sha256": sha(canonical(value))}


def identity(kind: str, value: str) -> dict[str, str]:
    return {"kind": kind, "value": value, "content_sha256": sha(value)}


def validator_identity(
    contract: str, entrypoint: str, modules: list[tuple[str, str]]
) -> str:
    return sha(
        canonical(
            {
                "contract": "task-witness-validator-artifact-manifest-v1",
                "validator_contract": contract,
                "entrypoint_module": entrypoint,
                "modules": [
                    {"name": name, "content_sha256": digest} for name, digest in modules
                ],
            }
        )
    )


class EvidenceError(ValueError):
    pass


class Witness:
    EvidenceError = EvidenceError
    canonical_bytes = staticmethod(canonical)
    digest = staticmethod(lambda value: sha(canonical(value)))
    dispatches: ClassVar[dict[str, dict[str, Any]]] = {}
    dispatch_bundles: ClassVar[
        dict[str, tuple[rolecasting_evidence.Bundle, dict[str, Any]]]
    ] = {}

    @staticmethod
    def absolute(path: Path, label: str) -> Path:
        if not path.is_absolute() or ".." in path.parts:
            raise EvidenceError(f"{label} must be absolute and traversal-free")
        return path

    @staticmethod
    def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != keys:
            raise EvidenceError(f"{label} schema drift")
        return value

    @staticmethod
    def text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise EvidenceError(f"{label} must be a non-empty string")
        return value

    @classmethod
    def token(cls, value: Any, label: str) -> str:
        value = cls.text(value, label)
        if not value[0].islower() or any(
            not (character.islower() or character.isdigit() or character == "-")
            for character in value
        ):
            raise EvidenceError(f"{label} is not a closed token")
        return value

    @classmethod
    def sha(cls, value: Any, label: str) -> str:
        value = cls.text(value, label)
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise EvidenceError(f"{label} must be a SHA-256 digest")
        return value

    @classmethod
    def identity(
        cls, value: Any, label: str, *, absent: bool = False
    ) -> dict[str, Any]:
        if absent and value == {"kind": "absent"}:
            return value
        value = cls.exact(value, {"kind", "value", "content_sha256"}, label)
        cls.text(value["kind"], f"{label}.kind")
        cls.text(value["value"], f"{label}.value")
        cls.sha(value["content_sha256"], f"{label}.content_sha256")
        return value

    @classmethod
    def document(
        cls,
        value: Any,
        keys: set[str],
        label: str,
        contract: str,
    ) -> dict[str, Any]:
        value = cls.exact(
            value,
            keys | {"schema_version", "contract", "content_sha256"},
            label,
        )
        unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["contract"] != contract
            or value["content_sha256"] != cls.digest(unsigned)
        ):
            raise EvidenceError(f"{label} contract mismatch")
        return value

    @classmethod
    def producer(
        cls, value: Any, snapshot: dict[str, Any], label: str
    ) -> dict[str, Any]:
        value = cls.exact(
            value, {"producer_id", "contract", "implementation_sha256"}, label
        )
        if value != snapshot["producer"]:
            raise EvidenceError(
                f"{label} is not accepted by the operator trust context"
            )
        return {
            **value,
            "validator_id": "tricritical-terminal-review-evidence-validator-v2",
            "validator_contract": "tricritical-terminal-review-evidence-v2",
            "validator_implementation_sha256": "f" * 64,
        }

    @classmethod
    def issuer(
        cls,
        value: Any,
        snapshot: dict[str, Any],
        label: str,
        capability: str,
    ) -> dict[str, Any]:
        value = cls.exact(
            value, {"issuer_id", "contract", "implementation_sha256"}, label
        )
        entry = next(
            (item for item in snapshot["issuers"] if item["identity"] == value), None
        )
        if entry is None or capability not in entry["capabilities"]:
            raise EvidenceError(f"{label} is not authorized for {capability}")
        return value

    @classmethod
    def invoke_registered_validator(
        cls, path: Path, _snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return copy.deepcopy(cls.dispatches[str(path)])
        except KeyError as error:
            raise EvidenceError(
                "Rolecasting dispatch bundle is unregistered"
            ) from error


class Bundle:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.files = {"manifest.json": raw_document(manifest)}

    @property
    def names(self) -> set[str]:
        return set(self.files)

    def read_json(self, name: str, label: str) -> tuple[dict[str, Any], bytes]:
        try:
            raw = self.files[name]
        except KeyError as error:
            raise EvidenceError(f"{label} is absent") from error
        value = json.loads(raw)
        if raw != raw_document(value):
            raise EvidenceError(f"{label} is not canonical")
        return value, raw


def load_validator() -> Any:
    specification = importlib.util.spec_from_file_location(
        "registered_tricritical_validator", VALIDATOR
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Tricritical validator could not be loaded")
    module = importlib.util.module_from_spec(specification)
    module.__dict__.update({"_TASK_WITNESS": Witness, "_VERIFIED_MODULES": {}})
    specification.loader.exec_module(module)
    return module


class TricriticalReviewEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        Witness.dispatches = {}
        Witness.dispatch_bundles = {}
        self.validator = load_validator()
        self.producer = {
            "producer_id": "tricritical-review-loop-v2",
            "contract": "tricritical-terminal-review-evidence-v2",
            "implementation_sha256": "1" * 64,
        }
        self.issuer = {
            "issuer_id": "tricritical-native-review-adapter-v1",
            "contract": "tricritical-native-review-adapter-v1",
            "implementation_sha256": "2" * 64,
        }
        self.trust = {
            "producer": self.producer,
            "issuers": [
                {
                    "identity": self.issuer,
                    "capabilities": ["feedback-observation", "terminal-verification"],
                }
            ],
        }
        self.candidate = identity("git-commit", "a" * 40)
        self.subject = {
            "candidate": self.candidate,
            "review_input": identity("review-input-sha256", "b" * 64),
            "requirements": identity("review-requirements-v1", "requirements"),
        }

    def role_subject(self, kind: str, payload: object) -> dict[str, str]:
        digest = sha(canonical(payload))
        return {"kind": kind, "value": digest, "content_sha256": digest}

    def store_manifest(self, bundle: Bundle, manifest: dict[str, Any]) -> None:
        bundle.files["manifest.json"] = raw_document(
            signed(
                {
                    key: value
                    for key, value in manifest.items()
                    if key != "content_sha256"
                }
            )
        )

    def terminal(
        self,
        state: str,
        owner: str,
        *,
        limitations: list[str] | None = None,
        missing: list[str] | None = None,
        unresolved: int = 0,
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "owner": owner,
            "limitations": [] if limitations is None else limitations,
            "missing_executions": [] if missing is None else missing,
            "unresolved_actionable_findings": unresolved,
            "verification": verification,
        }

    def finding(self, finding_id: str) -> dict[str, Any]:
        return {
            "finding_id": finding_id,
            "kind": "contract-defect",
            "evidence": [identity("finding-evidence-v1", finding_id)],
            "affected_contract": "review contract",
            "causal_path": "candidate to observable behavior",
            "impact": "incorrect behavior",
            "severity": "major",
            "direction": "correct the candidate",
            "proof_required": "targeted verification",
            "limitations": [],
            "residual_risk": "bounded",
        }

    def rebind_complete_cycle(
        self,
        bundle: Bundle,
        dispositions: list[str],
        *,
        report_limitations: list[str] | None = None,
        adjudication_limitations: list[str] | None = None,
        execution_mode: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        report_limitations = [] if report_limitations is None else report_limitations
        adjudication_limitations = (
            [] if adjudication_limitations is None else adjudication_limitations
        )
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        report_id = min(cycle["reports"])
        report = cycle["reports"][report_id]
        findings = [
            self.finding(f"finding-{index}") for index in range(len(dispositions))
        ]
        report["findings"] = findings
        report["limitations"] = report_limitations
        report = signed(
            {key: value for key, value in report.items() if key != "content_sha256"}
        )
        cycle["reports"][report_id] = report

        review = Witness.dispatches[cycle["review_dispatch"]["path"]]
        review_projection = review["projection"]
        execution = review_projection["executions"][report_id]
        execution["returned"]["content_sha256"] = sha(raw_document(report))
        review["projection"] = signed(
            {
                key: value
                for key, value in review_projection.items()
                if key != "content_sha256"
            }
        )
        review_projection = review["projection"]

        report_hashes = {
            execution_id: sha(raw_document(value))
            for execution_id, value in cycle["reports"].items()
        }
        ensemble = cycle["ensemble"]
        ensemble["reports"] = report_hashes
        ensemble["dispatch_projection_sha256"] = sha(canonical(review_projection))
        ensemble["limitations"] = report_limitations
        if execution_mode is not None:
            ensemble["completeness"]["execution_mode"] = execution_mode
        cycle["ensemble"] = signed(
            {key: value for key, value in ensemble.items() if key != "content_sha256"}
        )

        feedback = cycle["feedback"]
        feedback["freeze"]["reports_sha256"] = sha(canonical(report_hashes))
        feedback["freeze"]["ensemble_sha256"] = sha(raw_document(cycle["ensemble"]))
        cycle["feedback"] = signed(
            {key: value for key, value in feedback.items() if key != "content_sha256"}
        )

        source = f"{report_id}:{execution['result_sha256']}"
        finding_refs = [
            f"{source}:{finding['finding_id']}:{sha(canonical(finding))}"
            for finding in findings
        ]
        adjudication = cycle["adjudication"]
        adjudication["findings_sha256"] = sha(canonical(sorted(finding_refs)))
        adjudication["items"] = [
            {
                "finding_ref": finding_ref,
                "disposition": disposition,
                "evidence": [identity("adjudication-evidence-v1", str(index))],
                "next_owner": {
                    "accept": "reviser",
                    "reject": "none",
                    "already addressed": "none",
                    "stale": "none",
                    "duplicate": "none",
                    "needs operator decision": "operator",
                    "blocked": "blocker",
                    "follow-up outside scope": "follow-up",
                }[disposition],
            }
            for index, (finding_ref, disposition) in enumerate(
                zip(finding_refs, dispositions, strict=True)
            )
        ]
        adjudication["limitations"] = adjudication_limitations
        cycle["adjudication"] = signed(
            {
                key: value
                for key, value in adjudication.items()
                if key != "content_sha256"
            }
        )
        adjudication_dispatch = Witness.dispatches[
            cycle["adjudication_dispatch"]["path"]
        ]
        adjudication_projection = adjudication_dispatch["projection"]
        adjudicator = next(iter(adjudication_projection["executions"].values()))
        adjudicator["returned"]["content_sha256"] = sha(
            raw_document(cycle["adjudication"])
        )
        adjudication_projection["subject"] = self.role_subject(
            "tricritical-adjudication-subject-v1",
            {
                "subject": cycle["subject"],
                "findings_sha256": adjudication["findings_sha256"],
                "ensemble_sha256": sha(raw_document(cycle["ensemble"])),
                "feedback_sha256": sha(raw_document(cycle["feedback"])),
            },
        )
        adjudication_dispatch["projection"] = signed(
            {
                key: value
                for key, value in adjudication_projection.items()
                if key != "content_sha256"
            }
        )
        self.store_manifest(bundle, manifest)
        return manifest, finding_refs

    def revision_bundle(
        self, dispositions: list[str] | None = None
    ) -> tuple[Bundle, dict[str, Any]]:
        dispositions = ["accept"] if dispositions is None else dispositions
        self.trust["issuers"][0]["capabilities"].append("mutation-authority")
        successor = {
            "candidate": identity("git-commit", "c" * 40),
            "review_input": self.subject["review_input"],
            "requirements": self.subject["requirements"],
        }
        first_bundle, _ = self.clean_bundle(path_tag="-cycle-0")
        first_manifest, finding_refs = self.rebind_complete_cycle(
            first_bundle, dispositions
        )
        accepted_refs = [
            finding_ref
            for finding_ref, disposition in zip(finding_refs, dispositions, strict=True)
            if disposition == "accept"
        ]
        second_bundle, _ = self.clean_bundle(subject=successor, path_tag="-cycle-1")
        second_manifest = json.loads(second_bundle.files["manifest.json"])
        authority = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-mutation-authority-v1",
                "issuer": self.issuer,
                "subject": self.subject,
                "authority": identity("authority-scope-v1", "bounded-edit"),
                "scope": identity("mutation-scope-v1", "accepted-findings-only"),
                "declared_verification": identity(
                    "verification-contract-v1", "targeted"
                ),
            }
        )
        first = first_manifest["cycles"][0]
        first["revision"] = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-revision-v1",
                "subject": self.subject,
                "accepted_findings": accepted_refs,
                "mutation_authority_sha256": sha(raw_document(authority)),
                "pre_edit_candidate": self.subject["candidate"],
                "successor_subject": successor,
                "deletion_alternatives": ["retain only the required behavior"],
                "resolution_evidence": {
                    finding_ref: [identity("revision-evidence-v1", f"resolved-{index}")]
                    for index, finding_ref in enumerate(accepted_refs)
                },
                "verification": {
                    "status": "passed",
                    "before_candidate": self.subject["candidate"],
                    "after_candidate": successor["candidate"],
                    "evidence": identity("revision-verification-v1", "passed"),
                },
                "fresh_review_required": True,
                "clarified_requirements": None,
            }
        )
        first["verification"] = None
        first["budget"]["used"] = 1
        first["progress"] = {
            "status": "progressed",
            "evidence": [identity("material-progress-v1", "successor-c")],
        }
        first["mutation_authority"] = {
            "available": True,
            "receipt_sha256": sha(raw_document(authority)),
        }
        first["owner"] = "reviser"
        second = second_manifest["cycles"][0]
        second["budget"]["used"] = 1
        second["mutation_authority"] = {
            "available": True,
            "receipt_sha256": sha(raw_document(authority)),
        }
        manifest = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-terminal-review-evidence-v2",
                "producer": self.producer,
                "initial_subject": self.subject,
                "terminal_subject": successor,
                "mutation_authority": authority,
                "cycles": [first, second],
                "terminal": second_manifest["terminal"],
            }
        )
        return Bundle(manifest), successor

    def budget_fact(
        self,
        candidate: str,
        *,
        used: int,
        tranche_index: int = 0,
        origin: str = "default",
        extension: dict[str, Any] | None = None,
        revised: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        subject = {
            "candidate": identity("git-commit", candidate * 40),
            "review_input": self.subject["review_input"],
            "requirements": self.subject["requirements"],
        }
        successor = {
            **subject,
            "candidate": identity("git-commit", chr(ord(candidate) + 1) * 40),
        }
        fact = {
            "controls": {
                "budget": {
                    "tranche_size": 3,
                    "used": used,
                    "unit": "revised-successor",
                    "status": "exhausted" if used == 3 else "open",
                    "origin": origin,
                    "tranche_index": tranche_index,
                },
                "extension": (
                    {"operator_choice": None, "eligibility": None}
                    if extension is None
                    else extension
                ),
            },
            "revision": {"successor": successor} if revised else None,
            "ensemble": {"risk": {"tier": "ordinary"}},
        }
        return {"subject": subject}, fact

    def rolecasting(
        self,
        path: str,
        subject: dict[str, str],
        roles: list[str],
        returned: dict[str, dict[str, str]],
        *,
        candidate: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        candidate = self.candidate if candidate is None else candidate
        producer = {
            "producer_id": "rolecasting-bootstrap-dispatch-v3",
            "contract": "rolecasting-dispatch-evidence-v3",
            "implementation_sha256": sha("rolecasting producer"),
        }
        issuer = {
            "issuer_id": "rolecasting-bootstrap-adapter-v3",
            "contract": "rolecasting-bootstrap-adapter-v3",
            "implementation_sha256": sha("rolecasting adapter"),
        }
        route_issuer = rolecasting_evidence.transition_contract.route_issuer()
        trust = {
            "producers": [producer],
            "issuers": [
                {
                    "identity": issuer,
                    "capabilities": ["execution-result", "model"],
                },
                {
                    "identity": route_issuer,
                    "capabilities": ["route-evidence"],
                },
            ],
        }
        model_name = "gpt-5.6-sol"
        reasoning_effort = "high"
        capability = identity("model-catalog", "gpt-5.6-sol-high")
        transition_guard = rolecasting_evidence.transition_contract.load_module()
        dispatches: list[dict[str, Any]] = []
        artifacts: dict[str, dict[str, Any]] = {}
        plan_binding_sha256 = sha(f"{path}: immutable pre-actuation plan")
        for index, role in enumerate(roles):
            execution_id = f"{path.rsplit('/', 1)[-1]}-{index}"
            target = {
                "product_family": "codex",
                "surface": "chatgpt-codex",
                "executor": "codex",
                "version": "2026.08.13",
            }
            topology = {
                "relationship": "child",
                "ownership": "leader-owned",
                "transport": "native-tool",
            }
            scope = identity("review-scope-v1", role)
            request = identity("review-request-v1", role)
            authority = {
                "access": "read-only",
                "subdelegation": False,
                "external_action": False,
                "evidence": identity("dispatch-authority-v1", role),
            }
            isolation = {
                "session": f"session-{path}-{index}",
                "context": f"context-{path}-{index}",
                "enforceable": True,
            }
            assurance = {
                "target": "product-attested",
                "model": "product-attested",
                "topology": "product-attested",
                "authority": "product-attested",
                "execution_result": "product-attested",
                "evidence": identity("product-attestation-v1", role),
            }
            assurance_minimum = dict.fromkeys(
                (
                    "target",
                    "model",
                    "topology",
                    "authority",
                    "execution_result",
                ),
                "product-attested",
            )
            transition_event = rolecasting_evidence.transition_contract.event(
                "new-subagent",
                predecessor=None,
            )
            transition_event["task_sha256"] = subject["content_sha256"]
            transition_event["payload_sha256"] = request["content_sha256"]
            transition_event["plan_binding_sha256"] = plan_binding_sha256
            transition_event["actuation_id"] = execution_id
            transition_route = rolecasting_evidence.transition_contract.route(
                rolecasting_evidence.transition_contract.selection(model=model_name),
                transition_event,
            )
            transition_route["target"] = target
            transition_route["capability_sha256"] = capability["content_sha256"]
            rolecasting_evidence.transition_contract.seal_route(transition_route)
            transition = transition_guard.authorize_model_transition(
                None,
                transition_event,
                rolecasting_evidence.transition_contract.scope(),
                None,
                transition_route,
            )
            transition_raw = raw_document(transition)
            dispatch = {
                "execution_id": execution_id,
                "plan_binding_sha256": plan_binding_sha256,
                "role": role,
                "target": target,
                "topology": topology,
                "subject": subject,
                "candidate": candidate,
                "scope": scope,
                "request": request,
                "return_contract": returned[role]["kind"],
                "verification_contract": "review-verification-v1",
                "stop_contract": "review-stop-v1",
                "model_transition_sha256": sha(transition_raw),
                "model_sha256": "",
                "authority": authority,
                "user_authority": None,
                "isolation": isolation,
                "assurance": assurance,
                "assurance_minimum": assurance_minimum,
            }
            model = signed(
                {
                    "schema_version": 1,
                    "contract": "rolecasting-model-selection-receipt-v3",
                    "issuer": issuer,
                    "subject": subject,
                    "execution_id": execution_id,
                    "target": target,
                    "model": model_name,
                    "reasoning_effort": reasoning_effort,
                    "capability": {"status": "available", "evidence": capability},
                    "model_transition_sha256": sha(transition_raw),
                }
            )
            model_raw = raw_document(model)
            dispatch["model_sha256"] = sha(model_raw)
            dispatches.append(dispatch)
            artifacts[execution_id] = {
                "role": role,
                "transition": transition_raw,
                "model": model_raw,
                "dispatch": dispatch,
                "request": request,
                "target": target,
                "topology": topology,
                "authority": authority,
                "isolation": isolation,
                "assurance": assurance,
                "assurance_minimum": assurance_minimum,
            }

        plan = signed(
            {
                "schema_version": 1,
                "contract": "rolecasting-dispatch-plan-v3",
                "subject": subject,
                "plan_binding_sha256": plan_binding_sha256,
                "dispatches": dispatches,
            }
        )
        plan_raw = raw_document(plan)
        files = {"plan.json": plan_raw}
        transition_digests = {}
        model_digests = {}
        result_digests = {}
        for execution_id, artifact in artifacts.items():
            dispatch = artifact["dispatch"]
            result = signed(
                {
                    "schema_version": 1,
                    "contract": "rolecasting-execution-result-receipt-v3",
                    "issuer": issuer,
                    "subject": subject,
                    "execution_id": execution_id,
                    "plan_sha256": sha(plan_raw),
                    "dispatch_sha256": sha(canonical(dispatch)),
                    "model_sha256": sha(artifact["model"]),
                    "model_transition_sha256": sha(artifact["transition"]),
                    "request": artifact["request"],
                    "returned": returned[artifact["role"]],
                    "verification": identity(
                        "review-verification-v1", artifact["role"]
                    ),
                    "stop": identity("review-stop-v1", artifact["role"]),
                    "target": artifact["target"],
                    "topology": artifact["topology"],
                    "assurance": artifact["assurance"],
                    "assurance_minimum": artifact["assurance_minimum"],
                    "user_authority": None,
                    "session": artifact["isolation"]["session"],
                    "context": artifact["isolation"]["context"],
                    "authority": artifact["authority"],
                    "before_candidate": candidate,
                    "after_candidate": candidate,
                    "usable": True,
                }
            )
            result_raw = raw_document(result)
            transition_name = f"transition-{execution_id}.json"
            model_name_path = f"model-{execution_id}.json"
            result_name = f"result-{execution_id}.json"
            files[transition_name] = artifact["transition"]
            files[model_name_path] = artifact["model"]
            files[result_name] = result_raw
            transition_digests[execution_id] = sha(artifact["transition"])
            model_digests[execution_id] = sha(artifact["model"])
            result_digests[execution_id] = sha(result_raw)

        manifest = signed(
            {
                "schema_version": 1,
                "contract": "rolecasting-dispatch-evidence-v3",
                "producer": producer,
                "subject": subject,
                "plan_sha256": sha(plan_raw),
                "transitions": transition_digests,
                "models": model_digests,
                "results": result_digests,
            }
        )
        files["manifest.json"] = raw_document(manifest)
        rolecasting_bundle = rolecasting_evidence.Bundle(files)
        projection = rolecasting_evidence.load_validator()._validate_bundle(
            rolecasting_bundle,
            trust_snapshot=trust,
        )
        provider = json.loads(
            (
                REPOSITORY
                / "plugins"
                / "rolecasting"
                / "task-witness-provider.json"
            ).read_text()
        )
        validator = provider["validators"][0]
        bundle_sha256 = sha(path)
        envelope = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": bundle_sha256,
            "producer": {
                **projection["producer"],
                "validator_id": validator["validator_id"],
                "validator_contract": validator["contract"],
                "validator_implementation_sha256": validator[
                    "implementation_sha256"
                ],
            },
            "validator": {
                "validator_id": validator["validator_id"],
                "contract": validator["contract"],
                "implementation_sha256": validator["implementation_sha256"],
            },
            "projection": projection,
        }
        Witness.dispatch_bundles[path] = (rolecasting_bundle, trust)
        Witness.dispatches[path] = envelope
        return envelope

    def clean_bundle(
        self,
        *,
        subject: dict[str, Any] | None = None,
        path_tag: str = "",
    ) -> tuple[Bundle, dict[str, Any]]:
        subject = self.subject if subject is None else subject
        candidate = subject["candidate"]
        review_path = f"/evidence/review{path_tag}"
        review_name = review_path.rsplit("/", 1)[-1]
        adjudication_path = f"/evidence/adjudication{path_tag}"
        adjudication_name = adjudication_path.rsplit("/", 1)[-1]
        reports: dict[str, dict[str, Any]] = {}
        returned: dict[str, dict[str, str]] = {}
        roles = ["critic-intent", "critic-runtime", "critic-structure"]
        for index, role in enumerate(roles):
            execution_id = f"{review_name}-{index}"
            report = signed(
                {
                    "schema_version": 1,
                    "contract": "tricritical-raw-report-v1",
                    "subject": subject,
                    "execution_id": execution_id,
                    "findings": [],
                    "falsification_attempts": [f"{role} found no counterexample"],
                    "outcome": {"status": "complete", "usable": True, "failure": None},
                    "limitations": [],
                }
            )
            reports[execution_id] = report
            returned[role] = {
                "kind": "tricritical-raw-report-v1",
                "value": execution_id,
                "content_sha256": sha(raw_document(report)),
            }
        review_subject = self.role_subject("tricritical-review-subject-v1", subject)
        review = self.rolecasting(
            review_path, review_subject, roles, returned, candidate=candidate
        )
        report_hashes = {
            execution_id: sha(raw_document(report))
            for execution_id, report in reports.items()
        }
        ensemble = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-ensemble-v1",
                "subject": subject,
                "dispatch_bundle_sha256": review["bundle_sha256"],
                "dispatch_projection_sha256": sha(canonical(review["projection"])),
                "reports": report_hashes,
                "risk": {
                    "tier": "ordinary",
                    "tranche": 3,
                    "rationale": "bounded review",
                    "selected_axes": ["intent", "runtime", "structure"],
                    "selected_specialists": [],
                    "waived_specialists": [],
                },
                "completeness": {
                    "execution_mode": "independent",
                    "missing": [],
                    "failed": [],
                    "unusable": [],
                },
                "causal_synthesis": [],
                "limitations": [],
            }
        )
        feedback = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-external-feedback-v1",
                "issuer": self.issuer,
                "subject": subject,
                "freeze": {
                    "reports_sha256": sha(canonical(report_hashes)),
                    "ensemble_sha256": sha(raw_document(ensemble)),
                    "sequence": 1,
                },
                "acquisition": {"sequence": 2, "forge_state": "pre-forge"},
                "state": "not-applicable-pre-forge",
                "source": None,
                "findings": [],
                "reason": "pre-forge",
            }
        )
        adjudication = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-adjudication-v1",
                "subject": subject,
                "execution_id": f"{adjudication_name}-0",
                "findings_sha256": sha(canonical([])),
                "items": [],
                "causal_groups": [],
                "disagreements": [],
                "limitations": [],
            }
        )
        adjudication_subject = self.role_subject(
            "tricritical-adjudication-subject-v1",
            {
                "subject": subject,
                "findings_sha256": adjudication["findings_sha256"],
                "ensemble_sha256": sha(raw_document(ensemble)),
                "feedback_sha256": sha(raw_document(feedback)),
            },
        )
        adjudication_dispatch = self.rolecasting(
            adjudication_path,
            adjudication_subject,
            ["adjudicator"],
            {
                "adjudicator": {
                    "kind": "tricritical-adjudication-v1",
                    "value": f"{adjudication_name}-0",
                    "content_sha256": sha(raw_document(adjudication)),
                }
            },
        )
        verification = {
            "issuer": self.issuer,
            "status": "passed",
            "candidate": candidate,
            "evidence": identity("verification-evidence-v1", "clean"),
            "unchanged": True,
        }
        terminal = {
            "state": "clean",
            "owner": "none",
            "limitations": [],
            "missing_executions": [],
            "unresolved_actionable_findings": 0,
            "verification": {
                key: verification[key]
                for key in ("status", "candidate", "evidence", "unchanged")
            },
        }
        manifest = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-terminal-review-evidence-v2",
                "producer": self.producer,
                "initial_subject": subject,
                "terminal_subject": subject,
                "mutation_authority": None,
                "cycles": [
                    {
                        "subject": subject,
                        "review_dispatch": {
                            "path": review_path,
                            "bundle_sha256": review["bundle_sha256"],
                        },
                        "reports": reports,
                        "ensemble": ensemble,
                        "feedback": feedback,
                        "adjudication_dispatch": {
                            "path": adjudication_path,
                            "bundle_sha256": adjudication_dispatch["bundle_sha256"],
                        },
                        "adjudication": adjudication,
                        "revision": None,
                        "verification": verification,
                        "budget": {
                            "tranche_size": 3,
                            "used": 0,
                            "unit": "revised-successor",
                            "status": "open",
                            "origin": "default",
                            "tranche_index": 0,
                        },
                        "extension": {"operator_choice": None, "eligibility": None},
                        "progress": {"status": "fixed-point", "evidence": []},
                        "mutation_authority": {
                            "available": False,
                            "receipt_sha256": None,
                        },
                        "owner": "none",
                    }
                ],
                "terminal": terminal,
            }
        )
        expected = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-terminal-review-projection-v2",
                "evidence_contract": "tricritical-terminal-review-evidence-v2",
                "manifest_sha256": sha(raw_document(manifest)),
                "subject": subject,
                "review_profile": {
                    "contract": "tricritical-review-profile-v1",
                    "execution_mode": "independent",
                    "required_axes": ["intent", "runtime", "structure"],
                    "selected_specialists": [],
                },
                "final_dispatch": review["projection"],
                "terminal": terminal,
            }
        )
        return Bundle(manifest), expected

    def rewrite_review_assurance(
        self,
        bundle: Bundle,
        level: str,
    ) -> dict[str, Any]:
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        review_envelope = Witness.dispatches[cycle["review_dispatch"]["path"]]
        review_projection = review_envelope["projection"]
        for execution_id, execution in review_projection["executions"].items():
            execution["assurance"] = {
                "target": level,
                "model": level,
                "topology": level,
                "authority": level,
                "execution_result": level,
                "evidence": identity(
                    "dispatch-assurance-v2", f"{level}-{execution_id}"
                ),
            }
            execution["assurance_minimum"] = dict.fromkeys(
                (
                    "target",
                    "model",
                    "topology",
                    "authority",
                    "execution_result",
                ),
                level,
            )
        review_projection = signed(
            {
                key: value
                for key, value in review_projection.items()
                if key != "content_sha256"
            }
        )
        review_envelope["projection"] = review_projection
        ensemble = cycle["ensemble"]
        ensemble["dispatch_projection_sha256"] = sha(canonical(review_projection))
        cycle["ensemble"] = signed(
            {key: value for key, value in ensemble.items() if key != "content_sha256"}
        )
        feedback = cycle["feedback"]
        feedback["freeze"]["ensemble_sha256"] = sha(raw_document(cycle["ensemble"]))
        cycle["feedback"] = signed(
            {key: value for key, value in feedback.items() if key != "content_sha256"}
        )
        adjudication_path = cycle["adjudication_dispatch"]["path"]
        adjudication_envelope = Witness.dispatches[adjudication_path]
        adjudication_projection = adjudication_envelope["projection"]
        adjudication_projection["subject"] = self.role_subject(
            "tricritical-adjudication-subject-v1",
            {
                "subject": cycle["subject"],
                "findings_sha256": sha(canonical([])),
                "ensemble_sha256": sha(raw_document(cycle["ensemble"])),
                "feedback_sha256": sha(raw_document(cycle["feedback"])),
            },
        )
        adjudication_envelope["projection"] = signed(
            {
                key: value
                for key, value in adjudication_projection.items()
                if key != "content_sha256"
            }
        )
        self.store_manifest(bundle, manifest)
        return review_projection

    def test_bare_clean_requires_fresh_complete_independent_review_and_adjudication(
        self,
    ) -> None:
        bundle, expected = self.clean_bundle()

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection, expected)

    def test_rolecasting_fixture_uses_registered_v3_validator_projection(self) -> None:
        bundle, _ = self.clean_bundle()
        manifest = json.loads(bundle.files["manifest.json"])
        path = manifest["cycles"][0]["review_dispatch"]["path"]
        rolecasting_bundle, trust = Witness.dispatch_bundles[path]
        projection = rolecasting_evidence.load_validator()._validate_bundle(
            rolecasting_bundle,
            trust_snapshot=trust,
        )
        self.assertEqual(Witness.dispatches[path]["projection"], projection)
        for execution in projection["executions"].values():
            self.assertLessEqual(
                {
                    "model_transition_sha256",
                    "model_transition_authorization_sha256",
                    "model_transition_event",
                    "model_transition_task_sha256",
                },
                set(execution),
            )

    def test_controller_observed_dispatch_is_preserved_without_assurance_laundering(
        self,
    ) -> None:
        bundle, _ = self.clean_bundle(path_tag="-controller-observed")
        expected_dispatch = self.rewrite_review_assurance(bundle, "controller-observed")

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["final_dispatch"], expected_dispatch)
        self.assertEqual(
            {
                execution["assurance"]["execution_result"]
                for execution in projection["final_dispatch"]["executions"].values()
            },
            {"controller-observed"},
        )
        self.assertEqual(
            {
                minimum
                for execution in projection["final_dispatch"]["executions"].values()
                for minimum in execution["assurance_minimum"].values()
            },
            {"controller-observed"},
        )

    def test_owner_accepts_every_registered_rolecasting_assurance_tier(self) -> None:
        for level in ("product-attested", "controller-observed", "self-reported"):
            with self.subTest(level=level):
                bundle, _ = self.clean_bundle(path_tag=f"-{level}")
                expected_dispatch = self.rewrite_review_assurance(bundle, level)

                projection = self.validator._validate_bundle(
                    bundle, trust_snapshot=self.trust
                )

                self.assertEqual(projection["final_dispatch"], expected_dispatch)

    def test_provider_registers_only_the_exact_v2_owner_validator(self) -> None:
        source = VALIDATOR.read_bytes()
        source_sha256 = sha(source)
        expected = signed(
            {
                "schema_version": 1,
                "contract": "task-witness-provider-declaration-v1",
                "plugin_id": "tricritical",
                "publisher": "nisavid",
                "repository": "https://github.com/nisavid/agents",
                "authority_profile": "tricritical-cooperative-review-v1",
                "producers": [],
                "issuers": [],
                "validators": [
                    {
                        "validator_id": (
                            "tricritical-terminal-review-evidence-validator-v2"
                        ),
                        "contract": "tricritical-terminal-review-evidence-v2",
                        "implementation_sha256": validator_identity(
                            "tricritical-terminal-review-evidence-v2",
                            "validator",
                            [("validator", source_sha256)],
                        ),
                        "entrypoint": "validator",
                        "modules": [
                            {
                                "name": "validator",
                                "relative_path": (
                                    "skills/loop/scripts/review_evidence.py"
                                ),
                                "length": len(source),
                                "sha256": source_sha256,
                            }
                        ],
                        "lifecycle": {
                            "state": "active",
                            "usable_for_new_publication": True,
                        },
                    }
                ],
            }
        )

        self.assertEqual(json.loads(PROVIDER.read_bytes()), expected)

    def test_owner_accepts_alternate_model_because_model_policy_is_rolecasting_owned(
        self,
    ) -> None:
        bundle, expected = self.clean_bundle(path_tag="-alternate-model")
        manifest = json.loads(bundle.files["manifest.json"])
        for dispatch_key in ("review_dispatch", "adjudication_dispatch"):
            path = manifest["cycles"][0][dispatch_key]["path"]
            envelope = Witness.dispatches[path]
            projection = envelope["projection"]
            for execution in projection["executions"].values():
                execution["model"] = "registered-alternate-model"
                execution["reasoning_effort"] = "medium"
            envelope["projection"] = signed(
                {
                    key: value
                    for key, value in projection.items()
                    if key != "content_sha256"
                }
            )
        review = Witness.dispatches[manifest["cycles"][0]["review_dispatch"]["path"]]
        ensemble = manifest["cycles"][0]["ensemble"]
        ensemble["dispatch_projection_sha256"] = sha(canonical(review["projection"]))
        manifest["cycles"][0]["ensemble"] = signed(
            {key: value for key, value in ensemble.items() if key != "content_sha256"}
        )
        feedback = manifest["cycles"][0]["feedback"]
        feedback["freeze"]["ensemble_sha256"] = sha(
            raw_document(manifest["cycles"][0]["ensemble"])
        )
        manifest["cycles"][0]["feedback"] = signed(
            {key: value for key, value in feedback.items() if key != "content_sha256"}
        )
        adjudication_path = manifest["cycles"][0]["adjudication_dispatch"]["path"]
        adjudication_envelope = Witness.dispatches[adjudication_path]
        adjudication_projection = adjudication_envelope["projection"]
        adjudication_projection["subject"] = self.role_subject(
            "tricritical-adjudication-subject-v1",
            {
                "subject": manifest["cycles"][0]["subject"],
                "findings_sha256": sha(canonical([])),
                "ensemble_sha256": sha(raw_document(manifest["cycles"][0]["ensemble"])),
                "feedback_sha256": sha(raw_document(manifest["cycles"][0]["feedback"])),
            },
        )
        adjudication_envelope["projection"] = signed(
            {
                key: value
                for key, value in adjudication_projection.items()
                if key != "content_sha256"
            }
        )
        self.store_manifest(bundle, manifest)
        expected["final_dispatch"] = review["projection"]
        expected["manifest_sha256"] = sha(bundle.files["manifest.json"])
        expected = signed(
            {key: value for key, value in expected.items() if key != "content_sha256"}
        )

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection, expected)

    def test_unusable_execution_is_preserved_as_incomplete_without_later_phases(
        self,
    ) -> None:
        bundle, _ = self.clean_bundle()
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        execution_id = "review-1"
        report = cycle["reports"][execution_id]
        report["outcome"] = {
            "status": "unusable",
            "usable": False,
            "failure": identity("execution-failure-v1", "unusable-runtime-report"),
        }
        report["limitations"] = ["runtime report was unusable"]
        report = signed(
            {key: value for key, value in report.items() if key != "content_sha256"}
        )
        cycle["reports"][execution_id] = report
        review = Witness.dispatches["/evidence/review"]
        review["projection"]["executions"][execution_id]["usable"] = False
        review["projection"]["executions"][execution_id]["returned"][
            "content_sha256"
        ] = sha(raw_document(report))
        review_projection = review["projection"]
        review_projection["content_sha256"] = sha(
            canonical(
                {
                    key: value
                    for key, value in review_projection.items()
                    if key != "content_sha256"
                }
            )
        )
        ensemble = cycle["ensemble"]
        ensemble["reports"][execution_id] = sha(raw_document(report))
        ensemble["limitations"] = ["runtime report was unusable"]
        ensemble["completeness"]["execution_mode"] = "incomplete / non-clean"
        ensemble["completeness"]["unusable"] = [execution_id]
        ensemble["dispatch_projection_sha256"] = sha(canonical(review_projection))
        cycle["ensemble"] = signed(
            {key: value for key, value in ensemble.items() if key != "content_sha256"}
        )
        cycle["feedback"] = None
        cycle["adjudication_dispatch"] = None
        cycle["adjudication"] = None
        cycle["revision"] = None
        cycle["verification"] = None
        cycle["progress"] = {"status": "stopped", "evidence": []}
        cycle["owner"] = "reviewer"
        terminal = {
            "state": "incomplete / non-clean",
            "owner": "reviewer",
            "limitations": ["runtime report was unusable"],
            "missing_executions": [execution_id],
            "unresolved_actionable_findings": 0,
            "verification": None,
        }
        manifest["terminal"] = terminal
        bundle.files["manifest.json"] = raw_document(
            signed(
                {
                    key: value
                    for key, value in manifest.items()
                    if key != "content_sha256"
                }
            )
        )

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"], terminal)

    def test_missing_selected_axis_is_preserved_as_incomplete(self) -> None:
        bundle, _ = self.clean_bundle(path_tag="-missing-axis")
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        review = Witness.dispatches[cycle["review_dispatch"]["path"]]
        projection = review["projection"]
        missing_id = next(
            execution_id
            for execution_id, execution in projection["executions"].items()
            if execution["role"] == "critic-runtime"
        )
        del projection["executions"][missing_id]
        review["projection"] = signed(
            {key: value for key, value in projection.items() if key != "content_sha256"}
        )
        del cycle["reports"][missing_id]
        ensemble = cycle["ensemble"]
        del ensemble["reports"][missing_id]
        ensemble["dispatch_projection_sha256"] = sha(canonical(review["projection"]))
        ensemble["completeness"] = {
            "execution_mode": "incomplete / non-clean",
            "missing": ["critic-runtime"],
            "failed": [],
            "unusable": [],
        }
        cycle["ensemble"] = signed(
            {key: value for key, value in ensemble.items() if key != "content_sha256"}
        )
        for field in (
            "feedback",
            "adjudication_dispatch",
            "adjudication",
            "revision",
            "verification",
        ):
            cycle[field] = None
        cycle["progress"] = {"status": "stopped", "evidence": []}
        cycle["owner"] = "reviewer"
        expected = self.terminal(
            "incomplete / non-clean", "reviewer", missing=["critic-runtime"]
        )
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        result = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(result["terminal"], expected)

    def test_clean_degraded_aggregates_report_ensemble_and_adjudication_limits(
        self,
    ) -> None:
        bundle, _ = self.clean_bundle(path_tag="-degraded")
        manifest, _ = self.rebind_complete_cycle(
            bundle,
            [],
            report_limitations=["critic isolation unavailable"],
            adjudication_limitations=["adjudicator evidence was bounded"],
            execution_mode="non-independent / degraded",
        )
        cycle = manifest["cycles"][0]
        verification = {
            key: cycle["verification"][key]
            for key in ("status", "candidate", "evidence", "unchanged")
        }
        expected = self.terminal(
            "clean / degraded",
            "none",
            limitations=[
                "adjudicator evidence was bounded",
                "critic isolation unavailable",
            ],
            verification=verification,
        )
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"], expected)

    def test_failed_verification_is_derived_with_verifier_owner(self) -> None:
        bundle, _ = self.clean_bundle(path_tag="-failed")
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        cycle["verification"]["status"] = "failed"
        cycle["progress"] = {"status": "stopped", "evidence": []}
        cycle["owner"] = "verifier"
        verification = {
            key: cycle["verification"][key]
            for key in ("status", "candidate", "evidence", "unchanged")
        }
        expected = self.terminal(
            "failed_verification", "verifier", verification=verification
        )
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"], expected)

    def test_candidate_drift_is_blocked_not_failed_verification(self) -> None:
        bundle, _ = self.clean_bundle(path_tag="-drift")
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        cycle["verification"]["unchanged"] = False
        cycle["progress"] = {"status": "stopped", "evidence": []}
        cycle["owner"] = "verifier"
        verification = {
            key: cycle["verification"][key]
            for key in ("status", "candidate", "evidence", "unchanged")
        }
        expected = self.terminal("blocked", "verifier", verification=verification)
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"], expected)

    def test_candidate_drift_precedes_declared_verification_failure(self) -> None:
        bundle, _ = self.clean_bundle(path_tag="-drift-failed")
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        cycle["verification"].update({"status": "failed", "unchanged": False})
        cycle["progress"] = {"status": "stopped", "evidence": []}
        cycle["owner"] = "verifier"
        verification = {
            key: cycle["verification"][key]
            for key in ("status", "candidate", "evidence", "unchanged")
        }
        expected = self.terminal("blocked", "verifier", verification=verification)
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"], expected)

    def test_all_eight_dispositions_have_closed_owner_and_terminal_semantics(
        self,
    ) -> None:
        cases = {
            "accept": ("blocked", "operator", 1, None),
            "reject": ("clean", "none", 0, "passed"),
            "already addressed": ("clean", "none", 0, "passed"),
            "stale": ("clean", "none", 0, "passed"),
            "duplicate": ("clean", "none", 0, "passed"),
            "needs operator decision": (
                "needs operator decision",
                "operator",
                1,
                None,
            ),
            "blocked": ("blocked", "blocker", 1, None),
            "follow-up outside scope": ("clean", "none", 0, "passed"),
        }
        for index, (disposition, expected_values) in enumerate(cases.items()):
            with self.subTest(disposition=disposition):
                bundle, _ = self.clean_bundle(path_tag=f"-disposition-{index}")
                manifest, _ = self.rebind_complete_cycle(bundle, [disposition])
                cycle = manifest["cycles"][0]
                state, owner, unresolved, verification_status = expected_values
                if verification_status is None:
                    cycle["verification"] = None
                    verification = None
                    cycle["progress"] = {"status": "stopped", "evidence": []}
                else:
                    verification = {
                        key: cycle["verification"][key]
                        for key in ("status", "candidate", "evidence", "unchanged")
                    }
                cycle["owner"] = owner
                expected = self.terminal(
                    state,
                    owner,
                    unresolved=unresolved,
                    verification=verification,
                )
                manifest["terminal"] = expected
                self.store_manifest(bundle, manifest)

                projection = self.validator._validate_bundle(
                    bundle, trust_snapshot=self.trust
                )

                self.assertEqual(projection["terminal"], expected)

    def test_needs_operator_decision_precedes_blocked_and_accepted_findings(
        self,
    ) -> None:
        bundle, _ = self.clean_bundle(path_tag="-precedence")
        manifest, _ = self.rebind_complete_cycle(
            bundle, ["accept", "blocked", "needs operator decision"]
        )
        cycle = manifest["cycles"][0]
        cycle["verification"] = None
        cycle["progress"] = {"status": "stopped", "evidence": []}
        cycle["owner"] = "operator"
        expected = self.terminal("needs operator decision", "operator", unresolved=3)
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"], expected)

    def test_terminal_claim_cannot_override_derived_evidence(self) -> None:
        bundle, _ = self.clean_bundle(path_tag="-dishonest-terminal")
        manifest = json.loads(bundle.files["manifest.json"])
        manifest["terminal"]["state"] = "blocked"
        manifest["terminal"]["owner"] = "verifier"
        self.store_manifest(bundle, manifest)

        with self.assertRaisesRegex(EvidenceError, "not derived from evidence"):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_explicit_single_axis_subset_is_degraded_not_missing(self) -> None:
        bundle, _ = self.clean_bundle(path_tag="-subset")
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        keep = "critic-intent"
        review = Witness.dispatches[cycle["review_dispatch"]["path"]]
        projection = review["projection"]
        keep_id = next(
            execution_id
            for execution_id, execution in projection["executions"].items()
            if execution["role"] == keep
        )
        projection["executions"] = {keep_id: projection["executions"][keep_id]}
        review["projection"] = signed(
            {key: value for key, value in projection.items() if key != "content_sha256"}
        )
        cycle["reports"] = {keep_id: cycle["reports"][keep_id]}
        ensemble = cycle["ensemble"]
        ensemble["risk"]["selected_axes"] = ["intent"]
        ensemble["reports"] = {keep_id: sha(raw_document(cycle["reports"][keep_id]))}
        ensemble["dispatch_projection_sha256"] = sha(canonical(review["projection"]))
        ensemble["completeness"]["execution_mode"] = "independent"
        cycle["ensemble"] = signed(
            {key: value for key, value in ensemble.items() if key != "content_sha256"}
        )
        feedback = cycle["feedback"]
        feedback["freeze"]["reports_sha256"] = sha(canonical(ensemble["reports"]))
        feedback["freeze"]["ensemble_sha256"] = sha(raw_document(cycle["ensemble"]))
        cycle["feedback"] = signed(
            {key: value for key, value in feedback.items() if key != "content_sha256"}
        )
        adjudication_projection = Witness.dispatches[
            cycle["adjudication_dispatch"]["path"]
        ]["projection"]
        adjudication_projection["subject"] = self.role_subject(
            "tricritical-adjudication-subject-v1",
            {
                "subject": cycle["subject"],
                "findings_sha256": sha(canonical([])),
                "ensemble_sha256": sha(raw_document(cycle["ensemble"])),
                "feedback_sha256": sha(raw_document(cycle["feedback"])),
            },
        )
        Witness.dispatches[cycle["adjudication_dispatch"]["path"]]["projection"] = (
            signed(
                {
                    key: value
                    for key, value in adjudication_projection.items()
                    if key != "content_sha256"
                }
            )
        )
        verification = {
            key: cycle["verification"][key]
            for key in ("status", "candidate", "evidence", "unchanged")
        }
        expected = self.terminal("clean / degraded", "none", verification=verification)
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["review_profile"]["required_axes"], ["intent"])
        self.assertEqual(projection["terminal"], expected)

    def test_retained_unused_mutation_authority_is_reported_and_clean_can_finish(
        self,
    ) -> None:
        self.trust["issuers"][0]["capabilities"].append("mutation-authority")
        bundle, _ = self.clean_bundle(path_tag="-unused-authority")
        manifest = json.loads(bundle.files["manifest.json"])
        authority = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-mutation-authority-v1",
                "issuer": self.issuer,
                "subject": self.subject,
                "authority": identity("authority-scope-v1", "bounded-edit"),
                "scope": identity("mutation-scope-v1", "review-findings-only"),
                "declared_verification": identity(
                    "verification-contract-v1", "targeted"
                ),
            }
        )
        manifest["mutation_authority"] = authority
        manifest["cycles"][0]["mutation_authority"] = {
            "available": True,
            "receipt_sha256": sha(raw_document(authority)),
        }
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"]["state"], "clean")

    def test_accepted_finding_with_retained_authority_waits_for_reviser(self) -> None:
        self.trust["issuers"][0]["capabilities"].append("mutation-authority")
        bundle, _ = self.clean_bundle(path_tag="-accepted-authority")
        manifest, _ = self.rebind_complete_cycle(bundle, ["accept"])
        authority = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-mutation-authority-v1",
                "issuer": self.issuer,
                "subject": self.subject,
                "authority": identity("authority-scope-v1", "bounded-edit"),
                "scope": identity("mutation-scope-v1", "accepted-findings-only"),
                "declared_verification": identity(
                    "verification-contract-v1", "targeted"
                ),
            }
        )
        cycle = manifest["cycles"][0]
        manifest["mutation_authority"] = authority
        cycle["mutation_authority"] = {
            "available": True,
            "receipt_sha256": sha(raw_document(authority)),
        }
        cycle["verification"] = None
        cycle["progress"] = {"status": "stopped", "evidence": []}
        cycle["owner"] = "reviser"
        expected = self.terminal("blocked", "reviser", unresolved=1)
        manifest["terminal"] = expected
        self.store_manifest(bundle, manifest)

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["terminal"], expected)

    def test_reused_critic_or_adjudicator_isolation_is_rejected(self) -> None:
        bundle, _ = self.clean_bundle(path_tag="-reuse")
        manifest = json.loads(bundle.files["manifest.json"])
        cycle = manifest["cycles"][0]
        review_projection = Witness.dispatches[cycle["review_dispatch"]["path"]][
            "projection"
        ]
        adjudication = Witness.dispatches[cycle["adjudication_dispatch"]["path"]]
        adjudication_projection = adjudication["projection"]
        adjudicator = next(iter(adjudication_projection["executions"].values()))
        critic = next(iter(review_projection["executions"].values()))
        adjudicator["isolation"] = copy.deepcopy(critic["isolation"])
        adjudication["projection"] = signed(
            {
                key: value
                for key, value in adjudication_projection.items()
                if key != "content_sha256"
            }
        )

        with self.assertRaisesRegex(EvidenceError, "reuses execution isolation"):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_authority_bound_revision_requires_fresh_successor_review(self) -> None:
        bundle, successor = self.revision_bundle()

        projection = self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

        self.assertEqual(projection["subject"], successor)
        self.assertEqual(projection["terminal"]["state"], "clean")
        self.assertEqual(
            projection["final_dispatch"]["subject"],
            self.role_subject("tricritical-review-subject-v1", successor),
        )

    def test_every_revision_cycle_must_preserve_its_derived_reviser_owner(self) -> None:
        bundle, _ = self.revision_bundle()
        manifest = json.loads(bundle.files["manifest.json"])
        manifest["cycles"][0]["owner"] = "none"
        self.store_manifest(bundle, manifest)

        with self.assertRaisesRegex(EvidenceError, "cycle 0 owner is dishonest"):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_needs_operator_disposition_cannot_be_laundered_through_revision(
        self,
    ) -> None:
        bundle, _ = self.revision_bundle(["accept", "needs operator decision"])

        with self.assertRaisesRegex(
            EvidenceError,
            "terminal disposition requires revision and verification null",
        ):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_blocked_disposition_cannot_be_laundered_through_revision(self) -> None:
        bundle, _ = self.revision_bundle(["accept", "blocked"])

        with self.assertRaisesRegex(
            EvidenceError,
            "terminal disposition requires revision and verification null",
        ):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_budget_used_must_increment_for_each_distinct_revised_successor(
        self,
    ) -> None:
        bundle, _ = self.revision_bundle()
        manifest = json.loads(bundle.files["manifest.json"])
        manifest["cycles"][0]["budget"]["used"] = 0
        manifest["cycles"][1]["budget"]["used"] = 0
        self.store_manifest(bundle, manifest)

        with self.assertRaisesRegex(EvidenceError, "budget accounting"):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_revision_must_cover_every_accepted_finding_exactly(self) -> None:
        bundle, _ = self.revision_bundle()
        manifest = json.loads(bundle.files["manifest.json"])
        revision = manifest["cycles"][0]["revision"]
        revision["accepted_findings"] = []
        manifest["cycles"][0]["revision"] = signed(
            {key: value for key, value in revision.items() if key != "content_sha256"}
        )
        self.store_manifest(bundle, manifest)

        with self.assertRaisesRegex(EvidenceError, "cover accepted findings"):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_revision_cycle_cannot_reuse_prior_dispatch_isolation(self) -> None:
        bundle, _ = self.revision_bundle()
        manifest = json.loads(bundle.files["manifest.json"])
        first = manifest["cycles"][0]
        second = manifest["cycles"][1]
        first_projection = Witness.dispatches[first["review_dispatch"]["path"]][
            "projection"
        ]
        second_dispatch = Witness.dispatches[second["review_dispatch"]["path"]]
        second_projection = second_dispatch["projection"]
        first_execution = next(iter(first_projection["executions"].values()))
        second_execution = next(iter(second_projection["executions"].values()))
        second_execution["isolation"] = copy.deepcopy(first_execution["isolation"])
        second_dispatch["projection"] = signed(
            {
                key: value
                for key, value in second_projection.items()
                if key != "content_sha256"
            }
        )

        with self.assertRaisesRegex(EvidenceError, "reuses execution isolation"):
            self.validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_fourth_ordinary_revision_cannot_reset_default_budget(self) -> None:
        cycles: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        for candidate in ("a", "b", "c", "d"):
            cycle, fact = self.budget_fact(candidate, used=0)
            cycles.append(cycle)
            facts.append(fact)

        with self.assertRaisesRegex(EvidenceError, "budget accounting"):
            self.validator._validate_budget_chain(
                cycles, facts, self.subject, self.trust
            )

    def test_exhausted_budget_requires_exact_fresh_same_size_extension_authority(
        self,
    ) -> None:
        self.trust["issuers"][0]["capabilities"].extend(
            ["operator-choice", "extension-eligibility"]
        )
        cycles: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        revised_candidates: list[dict[str, Any]] = []
        for used, candidate in enumerate(("a", "b", "c"), start=1):
            cycle, fact = self.budget_fact(candidate, used=used)
            cycles.append(cycle)
            facts.append(fact)
            revised_candidates.append(fact["revision"]["successor"]["candidate"])
        extension_subject = {
            "candidate": identity("git-commit", "d" * 40),
            "review_input": self.subject["review_input"],
            "requirements": self.subject["requirements"],
        }
        choice = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-operator-choice-v1",
                "issuer": self.issuer,
                "subject": extension_subject,
                "choice": "extend",
                "tranche_index": 1,
                "tranche_size": 3,
                "synchronous": True,
                "timeout": None,
                "evidence": identity("operator-choice-evidence-v1", "fresh-choice"),
            }
        )
        eligibility = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-extension-eligibility-v1",
                "issuer": self.issuer,
                "subject": extension_subject,
                "eligible": True,
                "basis": "material-progress",
                "prior_candidates": revised_candidates,
                "clarified_requirements": None,
                "evidence": identity("extension-evidence-v1", "material-progress"),
            }
        )
        extension = {"operator_choice": choice, "eligibility": eligibility}
        fourth_cycle, fourth_fact = self.budget_fact(
            "d",
            used=1,
            tranche_index=1,
            origin="operator-extension",
            extension=extension,
        )
        cycles.append(fourth_cycle)
        cycles[-1]["subject"] = extension_subject
        facts.append(fourth_fact)

        self.validator._validate_budget_chain(cycles, facts, self.subject, self.trust)

        attacks = {
            "wrong-size": lambda value: value["controls"]["budget"].update(
                {"tranche_size": 2}
            ),
            "reused-index": lambda value: value["controls"]["budget"].update(
                {
                    "tranche_index": 0,
                    "origin": "default",
                    "used": 3,
                    "status": "exhausted",
                }
            ),
            "stale-choice": lambda value: value["controls"]["extension"][
                "operator_choice"
            ].update({"tranche_index": 0}),
            "stale-eligibility": lambda value: value["controls"]["extension"][
                "eligibility"
            ].update({"prior_candidates": revised_candidates[:-1]}),
        }
        for name, mutate in attacks.items():
            with self.subTest(attack=name):
                attacked = copy.deepcopy(facts)
                mutate(attacked[-1])
                with self.assertRaises(EvidenceError):
                    self.validator._validate_budget_chain(
                        cycles, attacked, self.subject, self.trust
                    )

    def test_default_tranche_mapping_is_exact_for_every_risk_tier(self) -> None:
        for tier, expected in {"low": 2, "ordinary": 3, "high": 5}.items():
            with self.subTest(tier=tier):
                cycle, fact = self.budget_fact("a", used=1)
                fact["ensemble"]["risk"]["tier"] = tier
                fact["controls"]["budget"]["tranche_size"] = expected
                fact["controls"]["budget"]["status"] = (
                    "exhausted" if expected == 1 else "open"
                )
                self.validator._validate_budget_chain(
                    [cycle], [fact], self.subject, self.trust
                )
                fact["controls"]["budget"]["tranche_size"] = expected + 1
                with self.assertRaisesRegex(EvidenceError, "default tranche size"):
                    self.validator._validate_budget_chain(
                        [cycle], [fact], self.subject, self.trust
                    )

    def test_initial_budget_override_requires_exact_synchronous_authority(self) -> None:
        self.trust["issuers"][0]["capabilities"].append("operator-choice")
        cycle, fact = self.budget_fact("a", used=1, origin="operator-initial-override")
        choice = signed(
            {
                "schema_version": 1,
                "contract": "tricritical-operator-choice-v1",
                "issuer": self.issuer,
                "subject": self.subject,
                "choice": "set-initial-tranche",
                "tranche_index": 0,
                "tranche_size": 3,
                "synchronous": True,
                "timeout": None,
                "evidence": identity("operator-choice-evidence-v1", "initial-override"),
            }
        )
        fact["controls"]["extension"] = {
            "operator_choice": choice,
            "eligibility": None,
        }

        self.validator._validate_budget_chain([cycle], [fact], self.subject, self.trust)

        for invalid in (0, -1, True):
            with self.subTest(invalid=invalid):
                attacked = copy.deepcopy(fact)
                attacked["controls"]["budget"]["tranche_size"] = invalid
                with self.assertRaises(EvidenceError):
                    self.validator._validate_budget_chain(
                        [cycle], [attacked], self.subject, self.trust
                    )


if __name__ == "__main__":
    unittest.main()
