from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.plugins.task_witness_client import test_launcher as task_witness_launcher
from tests.plugins.task_witness_deployment._support import load_deployment_module
from tests.plugins import test_rolecasting_model_transition as transition_contract

REPOSITORY = Path(__file__).resolve().parents[2]
BUNDLE_CONTRACT = "rolecasting-dispatch-evidence-v3"
REQUEST_CONTRACT = "rolecasting-bootstrap-dispatch-request-v3"
ISSUER_CONTRACT = "rolecasting-bootstrap-adapter-v3"
PRODUCER_ID = "rolecasting-bootstrap-dispatch-v3"
ISSUER_ID = "rolecasting-bootstrap-adapter-v3"
VALIDATOR_ID = "rolecasting-dispatch-evidence-validator-v3"
VALIDATOR = (
    REPOSITORY
    / "plugins"
    / "rolecasting"
    / "skills"
    / "delegating-cross-agent-work"
    / "scripts"
    / "dispatch_evidence.py"
)
ADAPTER = VALIDATOR.with_name("dispatch_adapter.py")

TARGET_PAIRS = (
    ("codex", "chatgpt-codex"),
    ("codex", "codex-cli-tui"),
    ("claude", "claude-code"),
    ("claude", "claude-desktop"),
    ("cursor", "cursor"),
    ("cursor", "cursor-agent"),
)
ASSURANCE_FIELDS = (
    "target",
    "model",
    "topology",
    "authority",
    "execution_result",
)


def canonical_value(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_document(value: object) -> bytes:
    return canonical_value(value) + b"\n"


def sha(raw: bytes | str) -> str:
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def signed(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "content_sha256": sha(canonical_value(value))}


def identity(kind: str, value: str) -> dict[str, str]:
    return {"kind": kind, "value": value, "content_sha256": sha(value)}


class EvidenceError(ValueError):
    pass


class Witness:
    EvidenceError = EvidenceError

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

    @staticmethod
    def digest(value: object) -> str:
        return sha(canonical_value(value))

    @classmethod
    def identity(cls, value: Any, label: str) -> dict[str, Any]:
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
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise EvidenceError(f"{label} contract mismatch")
        if value["contract"] != contract:
            raise EvidenceError(f"{label} contract mismatch")
        unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
        if value["content_sha256"] != cls.digest(unsigned):
            raise EvidenceError(f"{label} content digest mismatch")
        return value

    @classmethod
    def producer(
        cls, value: Any, snapshot: dict[str, Any], label: str
    ) -> dict[str, Any]:
        value = cls.exact(
            value, {"producer_id", "contract", "implementation_sha256"}, label
        )
        cls.token(value["producer_id"], f"{label}.producer_id")
        cls.text(value["contract"], f"{label}.contract")
        cls.sha(value["implementation_sha256"], f"{label}.implementation_sha256")
        if value not in snapshot["producers"]:
            raise EvidenceError(
                f"{label} is not accepted by the operator trust context"
            )
        return value

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
        cls.token(value["issuer_id"], f"{label}.issuer_id")
        cls.text(value["contract"], f"{label}.contract")
        cls.sha(value["implementation_sha256"], f"{label}.implementation_sha256")
        entry = next(
            (entry for entry in snapshot["issuers"] if entry["identity"] == value),
            None,
        )
        if entry is None or capability not in entry["capabilities"]:
            raise EvidenceError(f"{label} is not authorized for {capability}")
        return value


class Bundle:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    @property
    def names(self) -> set[str]:
        return set(self.files)

    def read_json(self, name: str, label: str) -> tuple[dict[str, Any], bytes]:
        try:
            raw = self.files[name]
        except KeyError as error:
            raise EvidenceError(
                f"{label} is absent from the captured bundle"
            ) from error
        value = json.loads(raw)
        if not isinstance(value, dict) or raw != canonical_document(value):
            raise EvidenceError(f"{label} must be a canonical JSON object")
        return value, raw


def load_validator() -> Any:
    specification = importlib.util.spec_from_file_location(
        "registered_task_validator", VALIDATOR
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Rolecasting validator could not be loaded")
    module = importlib.util.module_from_spec(specification)
    module.__dict__["_TASK_WITNESS"] = Witness
    module.__dict__["_VERIFIED_MODULES"] = {
        "model-transition": transition_contract.load_module()
    }
    specification.loader.exec_module(module)
    return module


def load_adapter() -> Any:
    specification = importlib.util.spec_from_file_location(
        "rolecasting_bootstrap_dispatch_adapter", ADAPTER
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Rolecasting bootstrap adapter could not be loaded")
    module = importlib.util.module_from_spec(specification)
    module.__dict__["_VERIFIED_MODULES"] = {
        "model-transition": transition_contract.load_module()
    }
    specification.loader.exec_module(module)
    return module


class RolecastingDispatchEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.producer = {
            "producer_id": PRODUCER_ID,
            "contract": BUNDLE_CONTRACT,
            "implementation_sha256": sha("rolecasting producer"),
        }
        self.issuer = {
            "issuer_id": ISSUER_ID,
            "contract": ISSUER_CONTRACT,
            "implementation_sha256": sha("rolecasting adapter"),
        }
        self.trust = {
            "producers": [self.producer],
            "issuers": [
                {
                    "identity": self.issuer,
                    "capabilities": ["execution-result", "model"],
                }
            ],
        }
        self.subject = identity("opaque-work-subject", "subject-one")
        self.candidate = identity("git-commit", "a" * 40)
        self.scope = identity("bounded-scope", "inspect-one-module")
        self.request = identity("dispatch-request", "request-one")
        self.authority = {
            "access": "read-only",
            "subdelegation": False,
            "external_action": False,
            "evidence": identity("operator-authority", "read-only-review"),
        }

    @staticmethod
    def target(
        product_family: str = "codex",
        surface: str = "chatgpt-codex",
    ) -> dict[str, str]:
        return {
            "product_family": product_family,
            "surface": surface,
            "executor": product_family,
            "version": "2026-08-13",
        }

    @staticmethod
    def topology(
        relationship: str = "child",
        ownership: str = "leader-owned",
        transport: str = "native-tool",
    ) -> dict[str, str]:
        return {
            "relationship": relationship,
            "ownership": ownership,
            "transport": transport,
        }

    @staticmethod
    def assurance(level: str = "product-attested") -> dict[str, Any]:
        return {
            **{field: level for field in ASSURANCE_FIELDS},
            "evidence": identity("dispatch-assurance", f"{level}-observation"),
        }

    @staticmethod
    def assurance_minimum(level: str = "product-attested") -> dict[str, str]:
        return {field: level for field in ASSURANCE_FIELDS}

    def make_bundle(
        self,
        *,
        target: dict[str, Any] | None = None,
        topology: dict[str, Any] | None = None,
        assurance: dict[str, Any] | None = None,
        assurance_minimum: dict[str, Any] | None = None,
        user_authority: dict[str, Any] | None = None,
        usable: bool = True,
    ) -> tuple[Bundle, dict[str, Any]]:
        target = target or self.target()
        topology = topology or self.topology()
        assurance = assurance or self.assurance()
        assurance_minimum = assurance_minimum or {
            field: assurance[field] for field in ASSURANCE_FIELDS
        }
        model_name = "gpt-5-6-sol"
        reasoning_effort = "high"
        capability_evidence = identity("model-catalog", "gpt-5-6-sol-high")
        transition_event = transition_contract.event(
            "new-subagent", predecessor=None
        )
        transition_event["payload_sha256"] = self.request["content_sha256"]
        transition_route = transition_contract.route(
            transition_contract.selection(model=model_name)
        )
        transition_route["target"] = target
        transition_route["capability_sha256"] = capability_evidence[
            "content_sha256"
        ]
        transition = transition_contract.load_module().authorize_model_transition(
            None,
            transition_event,
            transition_contract.scope(),
            None,
            transition_route,
        )
        transition_raw = canonical_document(transition)
        dispatch = {
            "execution_id": "worker-one",
            "role": "independent-checker",
            "target": target,
            "topology": topology,
            "subject": self.subject,
            "candidate": self.candidate,
            "scope": self.scope,
            "request": self.request,
            "return_contract": "worker-report-v1",
            "verification_contract": "worker-verification-v1",
            "stop_contract": "worker-stop-v1",
            "model_transition_sha256": sha(transition_raw),
            "model_sha256": "",
            "authority": self.authority,
            "user_authority": user_authority,
            "isolation": {
                "session": "session-one",
                "context": "context-one",
                "enforceable": True,
            },
            "assurance": assurance,
            "assurance_minimum": assurance_minimum,
        }
        model = signed(
            {
                "schema_version": 1,
                "contract": "rolecasting-model-selection-receipt-v3",
                "issuer": self.issuer,
                "subject": self.subject,
                "execution_id": "worker-one",
                "target": target,
                "model": model_name,
                "reasoning_effort": reasoning_effort,
                "capability": {
                    "status": "available",
                    "evidence": capability_evidence,
                },
                "model_transition_sha256": sha(transition_raw),
            }
        )
        model_raw = canonical_document(model)
        dispatch["model_sha256"] = sha(model_raw)
        plan = signed(
            {
                "schema_version": 1,
                "contract": "rolecasting-dispatch-plan-v3",
                "subject": self.subject,
                "dispatches": [dispatch],
            }
        )
        plan_raw = canonical_document(plan)
        result = signed(
            {
                "schema_version": 1,
                "contract": "rolecasting-execution-result-receipt-v3",
                "issuer": self.issuer,
                "subject": self.subject,
                "execution_id": "worker-one",
                "plan_sha256": sha(plan_raw),
                "dispatch_sha256": sha(canonical_value(dispatch)),
                "model_sha256": sha(model_raw),
                "model_transition_sha256": sha(transition_raw),
                "request": self.request,
                "returned": identity("worker-report-v1", "report-one"),
                "verification": identity("worker-verification-v1", "verification-one"),
                "stop": identity("worker-stop-v1", "done"),
                "target": target,
                "topology": topology,
                "assurance": assurance,
                "assurance_minimum": assurance_minimum,
                "user_authority": user_authority,
                "session": "session-one",
                "context": "context-one",
                "authority": self.authority,
                "before_candidate": self.candidate,
                "after_candidate": self.candidate,
                "usable": usable,
            }
        )
        result_raw = canonical_document(result)
        manifest = signed(
            {
                "schema_version": 1,
                "contract": BUNDLE_CONTRACT,
                "producer": self.producer,
                "subject": self.subject,
                "plan_sha256": sha(plan_raw),
                "transitions": {"worker-one": sha(transition_raw)},
                "models": {"worker-one": sha(model_raw)},
                "results": {"worker-one": sha(result_raw)},
            }
        )
        manifest_raw = canonical_document(manifest)
        files = {
            "manifest.json": manifest_raw,
            "plan.json": plan_raw,
            "transition-worker-one.json": transition_raw,
            "model-worker-one.json": model_raw,
            "result-worker-one.json": result_raw,
        }
        expected = signed(
            {
                "schema_version": 1,
                "contract": "rolecasting-dispatch-projection-v3",
                "evidence_contract": BUNDLE_CONTRACT,
                "manifest_sha256": sha(manifest_raw),
                "plan_sha256": sha(plan_raw),
                "subject": self.subject,
                "producer": self.producer,
                "executions": {
                    "worker-one": {
                        "execution_id": "worker-one",
                        "role": "independent-checker",
                        "target": target,
                        "topology": topology,
                        "candidate": self.candidate,
                        "scope": self.scope,
                        "request": self.request,
                        "return_contract": "worker-report-v1",
                        "verification_contract": "worker-verification-v1",
                        "stop_contract": "worker-stop-v1",
                        "model_transition_sha256": sha(transition_raw),
                        "model_transition_authorization_sha256": transition[
                            "authorization_sha256"
                        ],
                        "model_transition_event": "new-subagent",
                        "model_transition_task_sha256": transition_event[
                            "task_sha256"
                        ],
                        "authority": self.authority,
                        "user_authority": user_authority,
                        "isolation": dispatch["isolation"],
                        "assurance": assurance,
                        "assurance_minimum": assurance_minimum,
                        "model": model_name,
                        "reasoning_effort": reasoning_effort,
                        "dispatch_sha256": sha(canonical_value(dispatch)),
                        "model_sha256": sha(model_raw),
                        "result_sha256": sha(result_raw),
                        "model_issuer": self.issuer,
                        "result_issuer": self.issuer,
                        "returned": result["returned"],
                        "verification": result["verification"],
                        "stop": result["stop"],
                        "usable": usable,
                    }
                },
            }
        )
        return Bundle(files), expected

    @staticmethod
    def decoded(bundle: Bundle, name: str) -> dict[str, Any]:
        value = json.loads(bundle.files[name])
        assert isinstance(value, dict)
        return value

    @staticmethod
    def resign(value: dict[str, Any]) -> dict[str, Any]:
        return signed(
            {key: item for key, item in value.items() if key != "content_sha256"}
        )

    def adapter_request(self, bundle: Bundle) -> dict[str, Any]:
        plan = self.decoded(bundle, "plan.json")
        transition = self.decoded(bundle, "transition-worker-one.json")
        model = self.decoded(bundle, "model-worker-one.json")
        result = self.decoded(bundle, "result-worker-one.json")
        dispatch = plan["dispatches"][0]
        dispatch_keys = (
            "execution_id",
            "role",
            "target",
            "topology",
            "subject",
            "candidate",
            "scope",
            "request",
            "return_contract",
            "verification_contract",
            "stop_contract",
            "authority",
            "user_authority",
            "isolation",
            "assurance",
            "assurance_minimum",
        )
        return signed(
            {
                "schema_version": 1,
                "contract": REQUEST_CONTRACT,
                "producer": self.producer,
                "issuer": self.issuer,
                "subject": self.subject,
                "dispatches": [
                    {
                        **{key: dispatch[key] for key in dispatch_keys},
                        "model": model["model"],
                        "reasoning_effort": model["reasoning_effort"],
                        "model_capability": model["capability"],
                        "model_transition": transition,
                        "returned": result["returned"],
                        "verification": result["verification"],
                        "stop": result["stop"],
                        "before_candidate": result["before_candidate"],
                        "after_candidate": result["after_candidate"],
                        "usable": result["usable"],
                    }
                ],
            }
        )

    def relink(self, bundle: Bundle) -> None:
        transition_raw = bundle.files["transition-worker-one.json"]
        model = self.resign(self.decoded(bundle, "model-worker-one.json"))
        model["model_transition_sha256"] = sha(transition_raw)
        model = self.resign(model)
        model_raw = canonical_document(model)
        plan = self.decoded(bundle, "plan.json")
        plan["dispatches"][0]["model_transition_sha256"] = sha(transition_raw)
        plan["dispatches"][0]["model_sha256"] = sha(model_raw)
        plan = self.resign(plan)
        plan_raw = canonical_document(plan)
        result = self.decoded(bundle, "result-worker-one.json")
        result["plan_sha256"] = sha(plan_raw)
        result["dispatch_sha256"] = sha(canonical_value(plan["dispatches"][0]))
        result["model_sha256"] = sha(model_raw)
        result["model_transition_sha256"] = sha(transition_raw)
        result = self.resign(result)
        result_raw = canonical_document(result)
        manifest = self.decoded(bundle, "manifest.json")
        manifest["plan_sha256"] = sha(plan_raw)
        manifest["transitions"] = {"worker-one": sha(transition_raw)}
        manifest["models"] = {"worker-one": sha(model_raw)}
        manifest["results"] = {"worker-one": sha(result_raw)}
        manifest = self.resign(manifest)
        bundle.files.update(
            {
                "manifest.json": canonical_document(manifest),
                "plan.json": plan_raw,
                "model-worker-one.json": model_raw,
                "result-worker-one.json": result_raw,
            }
        )

    def relink_result(self, bundle: Bundle) -> None:
        result = self.resign(self.decoded(bundle, "result-worker-one.json"))
        result_raw = canonical_document(result)
        manifest = self.decoded(bundle, "manifest.json")
        manifest["results"] = {"worker-one": sha(result_raw)}
        bundle.files["manifest.json"] = canonical_document(self.resign(manifest))
        bundle.files["result-worker-one.json"] = result_raw

    def assert_bundle_rejected(self, bundle: Bundle, message: str) -> None:
        validator = load_validator()
        with self.assertRaisesRegex(EvidenceError, message):
            validator._validate_bundle(bundle, trust_snapshot=self.trust)

    def test_chatgpt_codex_product_attested_dispatch_projects_exactly(self) -> None:
        bundle, expected = self.make_bundle()

        projection = load_validator()._validate_bundle(
            bundle, trust_snapshot=self.trust
        )

        self.assertEqual(projection, expected)

    def test_codex_cli_controller_observed_dispatch_projects_exactly(self) -> None:
        bundle, expected = self.make_bundle(
            target=self.target("codex", "codex-cli-tui"),
            assurance=self.assurance("controller-observed"),
        )

        projection = load_validator()._validate_bundle(
            bundle, trust_snapshot=self.trust
        )

        self.assertEqual(projection, expected)

    def test_validator_accepts_all_closed_target_pairs_and_assurance_tiers(
        self,
    ) -> None:
        levels = ("product-attested", "controller-observed", "self-reported")
        for product_family, surface in TARGET_PAIRS:
            for level in levels:
                with self.subTest(
                    product_family=product_family, surface=surface, level=level
                ):
                    bundle, expected = self.make_bundle(
                        target=self.target(product_family, surface),
                        assurance=self.assurance(level),
                    )
                    projection = load_validator()._validate_bundle(
                        bundle, trust_snapshot=self.trust
                    )
                    self.assertEqual(projection, expected)

    def test_validator_preserves_mixed_assurance_and_unusable_execution(self) -> None:
        assurance = self.assurance()
        for field, level in zip(
            ASSURANCE_FIELDS,
            (
                "product-attested",
                "controller-observed",
                "self-reported",
                "product-attested",
                "controller-observed",
            ),
            strict=True,
        ):
            assurance[field] = level
        assurance_minimum = {
            "target": "controller-observed",
            "model": "self-reported",
            "topology": "self-reported",
            "authority": "product-attested",
            "execution_result": "self-reported",
        }
        bundle, expected = self.make_bundle(
            assurance=assurance,
            assurance_minimum=assurance_minimum,
            usable=False,
        )

        projection = load_validator()._validate_bundle(
            bundle, trust_snapshot=self.trust
        )

        self.assertEqual(projection, expected)
        self.assertIs(projection["executions"]["worker-one"]["usable"], False)
        self.assertEqual(
            projection["executions"]["worker-one"]["assurance_minimum"],
            assurance_minimum,
        )

    def test_bootstrap_adapter_deterministically_renders_supplied_execution_facts(
        self,
    ) -> None:
        adapter = load_adapter()
        adapter_sha = sha(ADAPTER.read_bytes())
        self.producer["implementation_sha256"] = adapter_sha
        self.issuer["implementation_sha256"] = adapter_sha
        cases = (
            (self.target(), self.assurance(), True),
            (
                self.target("codex", "codex-cli-tui"),
                self.assurance("controller-observed"),
                False,
            ),
        )
        for target, assurance, usable in cases:
            with self.subTest(surface=target["surface"], usable=usable):
                bundle, _ = self.make_bundle(
                    target=target, assurance=assurance, usable=usable
                )
                request = self.adapter_request(bundle)

                rendered = adapter.render_dispatch_bundle(request)

                self.assertEqual(rendered, bundle.files)

    def test_bootstrap_adapter_rejects_claims_it_cannot_truthfully_issue(self) -> None:
        adapter = load_adapter()
        adapter_sha = sha(ADAPTER.read_bytes())
        self.producer["implementation_sha256"] = adapter_sha
        self.issuer["implementation_sha256"] = adapter_sha
        bundle, _ = self.make_bundle()
        base = self.adapter_request(bundle)

        cases = (
            (
                "write authority",
                lambda value: value["dispatches"][0]["authority"].update(
                    access="read-write"
                ),
                "read-only",
            ),
            (
                "changed candidate",
                lambda value: value["dispatches"][0].update(
                    after_candidate=identity("git-commit", "b" * 40)
                ),
                "changed candidate",
            ),
            (
                "integer usable",
                lambda value: value["dispatches"][0].update(usable=0),
                "strict Boolean",
            ),
            (
                "unavailable model",
                lambda value: value["dispatches"][0]["model_capability"].update(
                    status="unknown"
                ),
                "not evidenced as available",
            ),
            (
                "invalid target pair",
                lambda value: value["dispatches"][0]["target"].update(
                    surface="claude-code"
                ),
                "target pair",
            ),
            (
                "invalid topology",
                lambda value: value["dispatches"][0]["topology"].update(
                    relationship="daemon"
                ),
                "relationship",
            ),
            (
                "invalid assurance",
                lambda value: value["dispatches"][0]["assurance"].update(
                    model="assumed"
                ),
                "assurance",
            ),
            (
                "invalid assurance minimum",
                lambda value: value["dispatches"][0]["assurance_minimum"].update(
                    model="assumed"
                ),
                "assurance",
            ),
            (
                "assurance below minimum",
                lambda value: value["dispatches"][0]["assurance"].update(
                    model="controller-observed"
                ),
                "below its assurance minimum",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name):
                changed = json.loads(json.dumps(base))
                mutate(changed)
                changed = self.resign(changed)
                with self.assertRaisesRegex(adapter.AdapterError, message):
                    adapter.render_dispatch_bundle(changed)

        for field, bad_actor in (
            (
                "producer",
                {**self.producer, "implementation_sha256": sha("other")},
            ),
            (
                "issuer",
                {**self.issuer, "implementation_sha256": sha("other")},
            ),
        ):
            with self.subTest(actor=field):
                changed = json.loads(json.dumps(base))
                changed[field] = bad_actor
                changed = self.resign(changed)
                with self.assertRaisesRegex(
                    adapter.AdapterError, "bound to this bootstrap adapter"
                ):
                    adapter.render_dispatch_bundle(changed)

    def test_transition_is_required_and_tampering_remains_rejected_after_relink(
        self,
    ) -> None:
        bundle, _ = self.make_bundle()
        del bundle.files["transition-worker-one.json"]
        self.assert_bundle_rejected(bundle, "model transition.*absent")

        bundle, _ = self.make_bundle()
        transition = self.decoded(bundle, "transition-worker-one.json")
        transition["request"]["route_evidence"]["selection"]["model"] = (
            "gpt-5.6-terra"
        )
        bundle.files["transition-worker-one.json"] = canonical_document(
            self.resign(transition)
        )
        self.relink(bundle)
        self.assert_bundle_rejected(bundle, "model transition is not authorized")

        adapter = load_adapter()
        adapter_sha = sha(ADAPTER.read_bytes())
        self.producer["implementation_sha256"] = adapter_sha
        self.issuer["implementation_sha256"] = adapter_sha
        bundle, _ = self.make_bundle()
        request = self.adapter_request(bundle)
        request["dispatches"][0]["model"] = "gpt-5.6-terra"
        with self.assertRaisesRegex(adapter.AdapterError, "selection mismatch"):
            adapter.render_dispatch_bundle(self.resign(request))

    def test_target_family_surface_pair_is_closed(self) -> None:
        cases = (
            ("codex", "codex-app-server"),
            ("codex", "claude-code"),
            ("claude", "cursor-agent"),
            ("unknown", "chatgpt-codex"),
        )
        for product_family, surface in cases:
            with self.subTest(product_family=product_family, surface=surface):
                bundle, _ = self.make_bundle()
                plan = self.decoded(bundle, "plan.json")
                plan["dispatches"][0]["target"].update(
                    product_family=product_family, surface=surface
                )
                bundle.files["plan.json"] = canonical_document(self.resign(plan))
                self.relink(bundle)
                self.assert_bundle_rejected(bundle, "target pair")

    def test_topology_enums_are_closed(self) -> None:
        cases = (
            ("relationship", "daemon"),
            ("ownership", "shared"),
            ("transport", "socket"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                bundle, _ = self.make_bundle()
                plan = self.decoded(bundle, "plan.json")
                plan["dispatches"][0]["topology"][field] = value
                bundle.files["plan.json"] = canonical_document(self.resign(plan))
                self.relink(bundle)
                self.assert_bundle_rejected(bundle, field)

    def test_user_authority_is_present_if_and_only_if_user_owned(self) -> None:
        bundle, expected = self.make_bundle(
            topology=self.topology(ownership="user-owned"),
            user_authority=identity("operator-authority", "create-task"),
        )
        self.assertEqual(
            load_validator()._validate_bundle(bundle, trust_snapshot=self.trust),
            expected,
        )

        cases = (
            (
                self.topology(),
                identity("operator-authority", "create-task"),
            ),
            (self.topology(ownership="user-owned"), None),
        )
        for topology, user_authority in cases:
            with self.subTest(ownership=topology["ownership"]):
                bundle, _ = self.make_bundle(
                    topology=topology, user_authority=user_authority
                )
                self.assert_bundle_rejected(bundle, "user authority")

    def test_isolation_and_assurance_schemas_are_closed(self) -> None:
        for field, value, message in (
            ("enforceable", False, "enforceable"),
            ("foreign_boundary", False, "schema drift"),
        ):
            with self.subTest(field=field):
                bundle, _ = self.make_bundle()
                plan = self.decoded(bundle, "plan.json")
                plan["dispatches"][0]["isolation"][field] = value
                bundle.files["plan.json"] = canonical_document(self.resign(plan))
                self.relink(bundle)
                self.assert_bundle_rejected(bundle, message)

        for field in ASSURANCE_FIELDS:
            with self.subTest(assurance=field):
                bundle, _ = self.make_bundle()
                plan = self.decoded(bundle, "plan.json")
                plan["dispatches"][0]["assurance"][field] = "assumed"
                bundle.files["plan.json"] = canonical_document(self.resign(plan))
                self.relink(bundle)
                self.assert_bundle_rejected(bundle, "assurance")

    def test_assurance_minimum_schema_and_order_are_closed(self) -> None:
        schema_cases = (
            (
                "missing dimension",
                lambda minimum: minimum.pop("model"),
                "schema drift",
            ),
            (
                "extra dimension",
                lambda minimum: minimum.update(evidence=identity("floor", "invalid")),
                "schema drift",
            ),
            (
                "non-token dimension",
                lambda minimum: minimum.update(model=1),
                "non-empty string",
            ),
            (
                "unknown dimension",
                lambda minimum: minimum.update(model="assumed"),
                "assurance is invalid",
            ),
        )
        for name, mutate, message in schema_cases:
            with self.subTest(name=name):
                bundle, _ = self.make_bundle()
                plan = self.decoded(bundle, "plan.json")
                mutate(plan["dispatches"][0]["assurance_minimum"])
                bundle.files["plan.json"] = canonical_document(self.resign(plan))
                self.relink(bundle)
                self.assert_bundle_rejected(bundle, message)

        for field in ASSURANCE_FIELDS:
            with self.subTest(downgraded_dimension=field):
                observed = self.assurance("product-attested")
                observed[field] = "self-reported"
                bundle, _ = self.make_bundle(
                    assurance=observed,
                    assurance_minimum=self.assurance_minimum("controller-observed"),
                )
                self.assert_bundle_rejected(bundle, "below its assurance minimum")

    def test_model_receipt_binds_target_and_availability_but_not_topology(self) -> None:
        bundle, _ = self.make_bundle()
        model = self.decoded(bundle, "model-worker-one.json")
        model["target"]["surface"] = "codex-cli-tui"
        bundle.files["model-worker-one.json"] = canonical_document(self.resign(model))
        self.relink(bundle)
        self.assert_bundle_rejected(bundle, "cross-bound")

        bundle, _ = self.make_bundle()
        model = self.decoded(bundle, "model-worker-one.json")
        model["topology"] = self.topology()
        bundle.files["model-worker-one.json"] = canonical_document(self.resign(model))
        self.relink(bundle)
        self.assert_bundle_rejected(bundle, "schema drift")

        bundle, _ = self.make_bundle()
        model = self.decoded(bundle, "model-worker-one.json")
        model["capability"]["status"] = "unknown"
        bundle.files["model-worker-one.json"] = canonical_document(self.resign(model))
        self.relink(bundle)
        self.assert_bundle_rejected(bundle, "unavailable")

    def test_result_receipt_binds_target_topology_assurance_and_user_authority(
        self,
    ) -> None:
        cases = (
            (
                "target",
                lambda result: result["target"].update(surface="codex-cli-tui"),
            ),
            (
                "topology",
                lambda result: result["topology"].update(relationship="peer"),
            ),
            (
                "assurance",
                lambda result: result["assurance"].update(model="self-reported"),
            ),
            (
                "assurance minimum",
                lambda result: result["assurance_minimum"].update(
                    model="controller-observed"
                ),
            ),
            (
                "user authority",
                lambda result: result.update(
                    user_authority=identity("operator-authority", "create-task")
                ),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                bundle, _ = self.make_bundle()
                result = self.decoded(bundle, "result-worker-one.json")
                mutate(result)
                bundle.files["result-worker-one.json"] = canonical_document(
                    self.resign(result)
                )
                self.relink_result(bundle)
                self.assert_bundle_rejected(bundle, "target, topology, or authority")

    def test_result_returns_and_strict_boolean_are_closed(self) -> None:
        cases = (
            ("request", identity("dispatch-request", "other"), "request mismatch"),
            ("returned", identity("other-v1", "report"), "contract mismatch"),
            ("verification", identity("other-v1", "verify"), "contract mismatch"),
            ("stop", identity("other-v1", "done"), "contract mismatch"),
            ("before_candidate", identity("git-commit", "b" * 40), "changed candidate"),
            ("after_candidate", identity("git-commit", "b" * 40), "changed candidate"),
            ("usable", 0, "strict Boolean"),
            ("usable", "false", "strict Boolean"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                bundle, _ = self.make_bundle()
                result = self.decoded(bundle, "result-worker-one.json")
                result[field] = value
                bundle.files["result-worker-one.json"] = canonical_document(
                    self.resign(result)
                )
                self.relink_result(bundle)
                self.assert_bundle_rejected(bundle, message)

    def test_validator_requires_purpose_specific_test_issuer_capabilities(self) -> None:
        bundle, _ = self.make_bundle()
        self.trust["issuers"][0]["capabilities"] = ["execution-result"]
        self.assert_bundle_rejected(bundle, "model")

        bundle, _ = self.make_bundle()
        self.trust["issuers"][0]["capabilities"] = ["model"]
        self.assert_bundle_rejected(bundle, "execution-result")

    def test_closed_bundle_and_canonical_document_bytes_are_required(self) -> None:
        bundle, _ = self.make_bundle()
        bundle.files["unexpected.json"] = canonical_document({"unexpected": True})
        self.assert_bundle_rejected(bundle, "missing or extra")

        bundle, _ = self.make_bundle()
        value = self.decoded(bundle, "model-worker-one.json")
        bundle.files["model-worker-one.json"] = (
            json.dumps(value, indent=2).encode() + b"\n"
        )
        self.assert_bundle_rejected(bundle, "canonical JSON")

    def test_alternate_test_trust_producer_and_nonreview_role_are_accepted(
        self,
    ) -> None:
        bundle, _ = self.make_bundle()
        alternate = {
            "producer_id": "rolecasting-historical-dispatch-v2",
            "contract": BUNDLE_CONTRACT,
            "implementation_sha256": sha("alternate producer"),
        }
        self.trust["producers"].append(alternate)
        manifest = self.decoded(bundle, "manifest.json")
        manifest["producer"] = alternate
        bundle.files["manifest.json"] = canonical_document(self.resign(manifest))
        plan = self.decoded(bundle, "plan.json")
        plan["dispatches"][0]["role"] = "documentation-auditor"
        bundle.files["plan.json"] = canonical_document(self.resign(plan))
        self.relink(bundle)

        projection = load_validator()._validate_bundle(
            bundle, trust_snapshot=self.trust
        )

        self.assertEqual(projection["producer"], alternate)
        self.assertEqual(
            projection["executions"]["worker-one"]["role"],
            "documentation-auditor",
        )

    def test_test_owned_bootstrap_trust_cannot_authorize_new_publication(self) -> None:
        bundle, expected = self.make_bundle()
        task_witness = task_witness_launcher.TaskWitnessLauncherTests()
        task_witness.setUp()
        self.addCleanup(task_witness.tearDown)
        for child in task_witness.bundle.iterdir():
            child.unlink()
        for name, raw in bundle.files.items():
            target = task_witness.bundle / name
            target.write_bytes(raw)
            target.chmod(0o600)
        task_witness.validator.write_bytes(VALIDATOR.read_bytes())
        task_witness.validator.chmod(0o600)
        transition_validator = task_witness.root / "model_transition.py"
        transition_validator.write_bytes(transition_contract.MODULE_PATH.read_bytes())
        transition_validator.chmod(0o600)
        validator_sha = sha(task_witness.validator.read_bytes())
        transition_validator_sha = sha(transition_validator.read_bytes())
        validator_implementation = task_witness_launcher.validator_identity(
            BUNDLE_CONTRACT,
            VALIDATOR_ID,
            [
                (VALIDATOR_ID, validator_sha),
                ("model-transition", transition_validator_sha),
            ],
        )
        publication_blocked = {
            "state": "active",
            "usable_for_new_publication": False,
        }
        validator_lifecycle = {
            "state": "active",
            "usable_for_new_publication": True,
        }
        trust = task_witness_launcher.document(
            {
                "schema_version": 1,
                "contract": "task-witness-trust-context-v2",
                "producers": [
                    {
                        **self.producer,
                        "validator_id": VALIDATOR_ID,
                        "validator_contract": BUNDLE_CONTRACT,
                        "validator_implementation_sha256": validator_implementation,
                        **publication_blocked,
                    }
                ],
                "issuers": [
                    {
                        **self.issuer,
                        "capabilities": ["execution-result", "model"],
                        **publication_blocked,
                    }
                ],
                "validators": [
                    {
                        "validator_id": VALIDATOR_ID,
                        "contract": BUNDLE_CONTRACT,
                        "implementation_sha256": validator_implementation,
                        "entrypoint": VALIDATOR_ID,
                        "modules": [
                            {
                                "name": VALIDATOR_ID,
                                "path": str(task_witness.validator),
                                "sha256": validator_sha,
                            },
                            {
                                "name": "model-transition",
                                "path": str(transition_validator),
                                "sha256": transition_validator_sha,
                            },
                        ],
                        **validator_lifecycle,
                    }
                ],
            }
        )
        task_witness.trust.write_bytes(task_witness_launcher.canonical(trust))
        task_witness.trust.chmod(0o600)

        live = task_witness.launch()
        historical = task_witness.launch(historical=True)

        self.assertNotEqual(live.returncode, 0)
        self.assertEqual(live.stdout, "")
        self.assertEqual(historical.returncode, 0, historical.stderr)
        envelope = json.loads(historical.stdout)
        self.assertEqual(envelope["contract"], "task-witness-launch-envelope-v1")
        self.assertEqual(envelope["witness"]["projection"], expected)

    def test_current_task_witness_keeps_bootstrap_authority_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            retained_trust = Path(temporary).resolve() / "retained-trust"

            provider = load_deployment_module().materialize_provider(
                REPOSITORY / "plugins" / "rolecasting",
                retained_trust,
            )

        self.assertIsNotNone(provider)
        assert provider is not None
        self.assertEqual(provider.plugin_id, "rolecasting")
        self.assertEqual(provider.producers, ())
        self.assertEqual(provider.issuers, ())


if __name__ == "__main__":
    unittest.main()
