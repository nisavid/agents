"""Deterministic, disclosure-safe retrieval benchmark evaluation."""

from dataclasses import dataclass
import hmac
import math
from pathlib import Path
import random
import re
from typing import Any, Iterable, Mapping, Sequence

from .canonical import StrictJsonError, canonical_scalar, digest, strict_json_loads
from .file_evidence import FileEvidenceError, read_file_evidence
from .model import deep_freeze


SCHEMA_VERSION = 1
CASE_KEYS = {
    "schema_version",
    "case_id",
    "query",
    "relevance",
    "must_recall",
    "must_not_return",
}
DIMENSION_KEYS = {
    "latency_ms_p95",
    "direct_cost_usd",
    "peak_memory_mb",
    "model_footprint_mb",
    "provider_available",
    "compatible",
    "license_ready",
}
LOWER_IS_BETTER = (
    "latency_ms_p95",
    "direct_cost_usd",
    "peak_memory_mb",
    "model_footprint_mb",
)
HIGHER_IS_BETTER = ("recall_at_20", "ndcg_at_10")
PROMOTION_REPORT_KEYS = {
    "schema_version",
    "candidate_id",
    "deployment_envelope",
    "dataset_digest",
    "metrics",
    "case_metrics",
    "confidence_intervals",
    "bootstrap",
    "gates",
    "dimensions",
}
MAX_DATASET_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_DATASET_CASES = 10_000
MAX_RETRIEVAL_ENTRIES_PER_CASE = 1_000
MAX_BOOTSTRAP_CASE_SAMPLES = 10_000_000
MAX_BENCHMARK_CANDIDATES = 1_000


class BenchmarkError(ValueError):
    """The artifact or evaluation input violates the benchmark contract."""


@dataclass(frozen=True)
class BenchmarkDataset:
    schema_version: int
    cases: tuple[Mapping[str, Any], ...]
    dataset_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != SCHEMA_VERSION
        ):
            raise BenchmarkError(
                f"dataset schema_version must be integer {SCHEMA_VERSION}"
            )
        if len(self.cases) > MAX_DATASET_CASES:
            raise BenchmarkError(
                f"benchmark dataset cannot exceed {MAX_DATASET_CASES} cases"
            )
        validated = [
            _validate_case(case, index)
            for index, case in enumerate(self.cases, 1)
        ]
        if not validated:
            raise BenchmarkError(
                "benchmark dataset must contain at least one case"
            )
        case_ids = [case["case_id"] for case in validated]
        if len(set(case_ids)) != len(case_ids):
            raise BenchmarkError("duplicate case_id in benchmark dataset")
        actual_digest = digest(validated)
        if self.dataset_digest != actual_digest:
            message = (
                "dataset digest mismatch: expected "
                f"{self.dataset_digest}, got {actual_digest}"
            )
            raise BenchmarkError(message)
        object.__setattr__(
            self, "cases", tuple(deep_freeze(case) for case in validated)
        )


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{label} must be a non-empty string")
    return value


def _bounded_number(
    value: Any,
    label: str,
    *,
    maximum: float | None = None,
) -> float:
    rule = (
        f"finite from 0 to {maximum:g}"
        if maximum is not None
        else "a finite non-negative number"
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkError(f"{label} must be {rule}")
    try:
        normalized = canonical_scalar(value)
        number = float(normalized)
    except (StrictJsonError, OverflowError) as error:
        raise BenchmarkError(f"{label} must be {rule}") from error
    if (
        not math.isfinite(number)
        or number < 0
        or (maximum is not None and number > maximum)
    ):
        raise BenchmarkError(f"{label} must be {rule}")
    return number


def _validate_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise BenchmarkError(f"{label} must be a list of non-empty strings")
    if len(set(value)) != len(value):
        raise BenchmarkError(f"{label} must not contain duplicates")
    return list(value)


def _validate_case(raw: Any, line_number: int) -> dict[str, Any]:
    label = f"benchmark line {line_number}"
    if not isinstance(raw, Mapping):
        raise BenchmarkError(f"{label} must be an object")
    if set(raw) != CASE_KEYS:
        raise BenchmarkError(
            f"{label} keys must be exactly {sorted(CASE_KEYS)}"
        )
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != SCHEMA_VERSION
    ):
        raise BenchmarkError(
            f"{label} schema_version must be integer {SCHEMA_VERSION}"
        )
    case_id = _require_nonempty_string(raw["case_id"], f"{label} case_id")
    query = _require_nonempty_string(raw["query"], f"{label} query")
    relevance = raw["relevance"]
    if not isinstance(relevance, Mapping) or not relevance:
        raise BenchmarkError(f"{label} relevance must be a non-empty object")
    normalized_relevance: dict[str, int] = {}
    for document_id, grade in relevance.items():
        _require_nonempty_string(document_id, f"{label} relevance document ID")
        if type(grade) is not int or not 1 <= grade <= 3:
            raise BenchmarkError(
                f"{label} relevance grades must be integers from 1 to 3"
            )
        normalized_relevance[document_id] = grade
    must_recall = _validate_string_list(
        raw["must_recall"], f"{label} must_recall"
    )
    if not must_recall:
        raise BenchmarkError(f"{label} must_recall must not be empty")
    if len(must_recall) > 20:
        raise BenchmarkError(
            f"{label} must_recall cannot exceed the top-20 retrieval gate"
        )
    must_not_return = _validate_string_list(
        raw["must_not_return"], f"{label} must_not_return"
    )
    if not set(must_recall).issubset(normalized_relevance):
        raise BenchmarkError(
            f"{label} must_recall documents require positive relevance "
            "judgments"
        )
    if set(must_not_return) & set(normalized_relevance):
        raise BenchmarkError(
            f"{label} must_not_return documents cannot be relevant"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "query": query,
        "relevance": normalized_relevance,
        "must_recall": must_recall,
        "must_not_return": must_not_return,
    }


def load_cases(path: str | Path, *, expected_digest: str) -> BenchmarkDataset:
    """Load and digest canonical benchmark cases from a JSON Lines artifact."""
    if not isinstance(expected_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_digest
    ):
        raise BenchmarkError(
            "expected dataset digest must be a lowercase SHA-256 digest"
        )
    cases: list[dict[str, Any]] = []
    try:
        artifact = Path(path).expanduser().resolve()
        payload, _evidence_digest = read_file_evidence(
            artifact,
            "benchmark dataset artifact",
            max_bytes=MAX_DATASET_ARTIFACT_BYTES,
        )
        lines = payload.decode("utf-8").split("\n")
    except FileEvidenceError as error:
        if "too large" in str(error):
            raise BenchmarkError(
                "benchmark dataset artifact exceeds the byte limit"
            ) from error
        raise BenchmarkError(f"cannot read benchmark dataset: {error}") from error
    except (OSError, UnicodeError) as error:
        raise BenchmarkError(
            f"cannot read benchmark dataset: {error}"
        ) from error
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = strict_json_loads(line)
        except ValueError as error:
            raise BenchmarkError(
                f"benchmark line {line_number} is not valid JSON: "
                f"{getattr(error, 'msg', str(error))}"
            ) from error
        cases.append(_validate_case(raw, line_number))
        if len(cases) > MAX_DATASET_CASES:
            raise BenchmarkError(
                f"benchmark dataset cannot exceed {MAX_DATASET_CASES} cases"
            )
    if not cases:
        raise BenchmarkError("benchmark dataset must contain at least one case")
    case_ids = [case["case_id"] for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise BenchmarkError("duplicate case_id in benchmark dataset")
    dataset_digest = digest(cases)
    if dataset_digest != expected_digest:
        message = (
            "dataset digest mismatch: expected "
            f"{expected_digest}, got {dataset_digest}"
        )
        raise BenchmarkError(message)
    return BenchmarkDataset(SCHEMA_VERSION, tuple(cases), dataset_digest)


def _validate_dimensions(raw: Any) -> dict[str, float | bool]:
    if not isinstance(raw, Mapping) or set(raw) != DIMENSION_KEYS:
        raise BenchmarkError(
            f"dimensions keys must be exactly {sorted(DIMENSION_KEYS)}"
        )
    dimensions: dict[str, float | bool] = {}
    for key in LOWER_IS_BETTER:
        dimensions[key] = _bounded_number(raw[key], f"dimension {key}")
    for key in ("provider_available", "compatible", "license_ready"):
        if type(raw[key]) is not bool:
            raise BenchmarkError(f"dimension {key} must be boolean")
        dimensions[key] = raw[key]
    return dimensions


def _validate_candidate(dataset: BenchmarkDataset, raw: Any) -> dict[str, Any]:
    required = {
        "candidate_id",
        "deployment_envelope",
        "retrievals",
        "dimensions",
        "policy_passed",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise BenchmarkError(
            f"candidate keys must be exactly {sorted(required)}"
        )
    candidate_id = _require_nonempty_string(raw["candidate_id"], "candidate_id")
    envelope = _require_nonempty_string(
        raw["deployment_envelope"], "deployment_envelope"
    )
    if type(raw["policy_passed"]) is not bool:
        raise BenchmarkError("policy_passed must be boolean")
    retrievals = raw["retrievals"]
    expected_case_ids = {case["case_id"] for case in dataset.cases}
    if (
        not isinstance(retrievals, Mapping)
        or set(retrievals) != expected_case_ids
    ):
        raise BenchmarkError(
            "retrievals must contain exactly the benchmark case IDs"
        )
    normalized_retrievals: dict[str, list[str]] = {}
    for case_id in sorted(expected_case_ids):
        retrieved = _validate_string_list(
            retrievals[case_id], f"retrievals.{case_id}"
        )
        if len(retrieved) > MAX_RETRIEVAL_ENTRIES_PER_CASE:
            raise BenchmarkError(
                f"retrievals.{case_id} cannot exceed "
                f"{MAX_RETRIEVAL_ENTRIES_PER_CASE} entries"
            )
        normalized_retrievals[case_id] = retrieved
    return {
        "candidate_id": candidate_id,
        "deployment_envelope": envelope,
        "retrievals": normalized_retrievals,
        "dimensions": _validate_dimensions(raw["dimensions"]),
        "policy_passed": raw["policy_passed"],
    }


def _recall(
    relevance: Mapping[str, int], retrieved: Sequence[str], limit: int
) -> float:
    return len(set(retrieved[:limit]) & set(relevance)) / len(relevance)


def _ndcg(
    relevance: Mapping[str, int], retrieved: Sequence[str], limit: int
) -> float:
    dcg = sum(
        (2.0 ** relevance.get(document_id, 0) - 1.0) / math.log2(rank + 1.0)
        for rank, document_id in enumerate(retrieved[:limit], 1)
    )
    ideal = sum(
        (2.0**grade - 1.0) / math.log2(rank + 1.0)
        for rank, grade in enumerate(
            sorted(relevance.values(), reverse=True)[:limit], 1
        )
    )
    return dcg / ideal


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _empirical_interval(
    values: Sequence[float], confidence: float
) -> list[float]:
    ordered = sorted(values)
    alpha = (1.0 - confidence) / 2.0
    lower_index = max(0, math.floor(alpha * len(ordered)))
    upper_index = min(
        len(ordered) - 1, math.ceil((1.0 - alpha) * len(ordered)) - 1
    )
    return [ordered[lower_index], ordered[upper_index]]


def _bootstrap_intervals(
    per_case: Mapping[str, Sequence[float]],
    *,
    seed: int,
    samples: int,
    confidence: float,
) -> dict[str, list[float]]:
    if type(seed) is not int:
        raise BenchmarkError("bootstrap seed must be an integer")
    if type(samples) is not int or not 1 <= samples <= 100_000:
        raise BenchmarkError(
            "bootstrap samples must be an integer from 1 to 100000"
        )
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 < confidence < 1.0
    ):
        raise BenchmarkError("bootstrap confidence must be between 0 and 1")
    if not isinstance(per_case, Mapping) or not per_case:
        raise BenchmarkError("bootstrap metrics must be a non-empty object")
    case_counts = {len(values) for values in per_case.values()}
    if len(case_counts) != 1 or not case_counts or next(iter(case_counts)) < 1:
        raise BenchmarkError(
            "bootstrap metrics must contain equal non-empty case sequences"
        )
    case_count = next(iter(case_counts))
    if samples * case_count > MAX_BOOTSTRAP_CASE_SAMPLES:
        raise BenchmarkError(
            "bootstrap samples multiplied by cases exceed the work limit"
        )
    generator = random.Random(seed)
    sampled: dict[str, list[float]] = {metric: [] for metric in per_case}
    for _ in range(samples):
        indices = [generator.randrange(case_count) for _ in range(case_count)]
        for metric, values in per_case.items():
            sampled[metric].append(_mean([values[index] for index in indices]))
    return {
        metric: _empirical_interval(values, float(confidence))
        for metric, values in sampled.items()
    }


def evaluate_candidate(
    dataset: BenchmarkDataset,
    candidate: Mapping[str, Any],
    *,
    seed: int,
    bootstrap_samples: int = 2000,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Evaluate ordered retrievals against authoritative judgments."""
    if not isinstance(dataset, BenchmarkDataset):
        raise BenchmarkError(f"dataset schema_version must be {SCHEMA_VERSION}")
    dataset = BenchmarkDataset(
        dataset.schema_version, dataset.cases, dataset.dataset_digest
    )
    value = _validate_candidate(dataset, candidate)
    recalls: list[float] = []
    ndcgs: list[float] = []
    must_recall_failures: list[dict[str, Any]] = []
    must_not_return_failures: list[dict[str, Any]] = []
    for case in dataset.cases:
        case_id = case["case_id"]
        retrieved = value["retrievals"][case_id]
        retrieved_ranks = {
            document_id: rank
            for rank, document_id in enumerate(retrieved, 1)
        }
        recalls.append(_recall(case["relevance"], retrieved, 20))
        ndcgs.append(_ndcg(case["relevance"], retrieved, 10))
        top_twenty = set(retrieved[:20])
        for document_id in case["must_recall"]:
            if document_id not in top_twenty:
                must_recall_failures.append(
                    {"case_id": case_id, "document_id": document_id}
                )
        for document_id in case["must_not_return"]:
            if document_id in retrieved_ranks:
                must_not_return_failures.append(
                    {
                        "case_id": case_id,
                        "document_id": document_id,
                        "rank": retrieved_ranks[document_id],
                    }
                )
    intervals = _bootstrap_intervals(
        {"recall_at_20": recalls, "ndcg_at_10": ndcgs},
        seed=seed,
        samples=bootstrap_samples,
        confidence=confidence,
    )
    must_recall_passed = not must_recall_failures
    must_not_return_passed = not must_not_return_failures
    policy_passed = value["policy_passed"]
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": value["candidate_id"],
        "deployment_envelope": value["deployment_envelope"],
        "dataset_digest": dataset.dataset_digest,
        "metrics": {
            "recall_at_20": _mean(recalls),
            "ndcg_at_10": _mean(ndcgs),
        },
        "case_metrics": {
            "case_ids": [case["case_id"] for case in dataset.cases],
            "recall_at_20": list(recalls),
            "ndcg_at_10": list(ndcgs),
        },
        "confidence_intervals": intervals,
        "bootstrap": {
            "seed": seed,
            "samples": bootstrap_samples,
            "confidence": float(confidence),
        },
        "gates": {
            "must_recall": {
                "passed": must_recall_passed,
                "failures": must_recall_failures,
            },
            "must_not_return": {
                "passed": must_not_return_passed,
                "failures": must_not_return_failures,
            },
            "policy": {"passed": policy_passed},
            "passed": must_recall_passed
            and must_not_return_passed
            and policy_passed,
        },
        "dimensions": value["dimensions"],
    }


def _ready(report: Mapping[str, Any]) -> bool:
    dimensions = report["dimensions"]
    return bool(
        report["gates"]["passed"]
        and dimensions["provider_available"]
        and dimensions["compatible"]
        and dimensions["license_ready"]
    )


def _dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    at_least_as_good = all(
        left["metrics"][key] >= right["metrics"][key]
        for key in HIGHER_IS_BETTER
    ) and all(
        left["dimensions"][key] <= right["dimensions"][key]
        for key in LOWER_IS_BETTER
    )
    strictly_better = any(
        left["metrics"][key] > right["metrics"][key] for key in HIGHER_IS_BETTER
    ) or any(
        left["dimensions"][key] < right["dimensions"][key]
        for key in LOWER_IS_BETTER
    )
    return at_least_as_good and strictly_better


def pareto_frontier(
    reports: Iterable[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Return ready, non-dominated candidates per deployment envelope."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    identities: set[tuple[str, str]] = set()
    dataset_by_envelope: dict[str, str] = {}
    for index, report in enumerate(reports, 1):
        if index > MAX_BENCHMARK_CANDIDATES:
            raise BenchmarkError("benchmark candidate count exceeds the limit")
        report = _validate_promotion_report(report, f"report {index}")
        envelope = report["deployment_envelope"]
        dataset_digest = report["dataset_digest"]
        expected_dataset = dataset_by_envelope.setdefault(
            envelope, dataset_digest
        )
        if not hmac.compare_digest(expected_dataset, dataset_digest):
            raise BenchmarkError(
                "reports within a deployment envelope require one "
                "dataset_digest"
            )
        identity = (envelope, report["candidate_id"])
        if identity in identities:
            raise BenchmarkError(
                "duplicate candidate_id within deployment envelope"
            )
        identities.add(identity)
        if _ready(report):
            grouped.setdefault(envelope, []).append(report)
    frontiers: dict[str, list[str]] = {}
    for envelope, candidates in sorted(grouped.items()):
        candidates = sorted(
            candidates,
            key=lambda candidate: candidate["candidate_id"],
        )
        frontiers[envelope] = sorted(
            candidate["candidate_id"]
            for candidate in candidates
            if not any(
                other is not candidate and _dominates(other, candidate)
                for other in candidates
            )
        )
    return frontiers


def _validate_thresholds(raw: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "max_retrieval_regression",
        "meaningful_gain",
    }:
        raise BenchmarkError(
            "promotion thresholds require max_retrieval_regression and "
            "meaningful_gain"
        )
    expected = {
        "max_retrieval_regression": set(HIGHER_IS_BETTER),
        "meaningful_gain": {
            "recall_at_20",
            "ndcg_at_10",
            "latency_ms_p95",
            "direct_cost_usd",
            "peak_memory_mb",
            "model_footprint_mb",
        },
    }
    normalized: dict[str, dict[str, float]] = {}
    for group, keys in expected.items():
        values = raw[group]
        if not isinstance(values, Mapping) or set(values) != keys:
            message = (
                f"promotion threshold {group} keys must be exactly "
                f"{sorted(keys)}"
            )
            raise BenchmarkError(message)
        normalized[group] = {}
        for key, value in values.items():
            normalized[group][key] = _bounded_number(
                value, f"promotion threshold {group}.{key}"
            )
    return normalized


def _validate_promotion_report(
    raw: Any, label: str
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != PROMOTION_REPORT_KEYS:
        raise BenchmarkError(
            f"{label} promotion report keys must be exactly "
            f"{sorted(PROMOTION_REPORT_KEYS)}"
        )
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise BenchmarkError(f"{label} schema_version must be integer 1")
    _require_nonempty_string(raw["candidate_id"], f"{label} candidate_id")
    _require_nonempty_string(
        raw["deployment_envelope"], f"{label} deployment_envelope"
    )
    if not isinstance(raw["dataset_digest"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", raw["dataset_digest"]
    ):
        raise BenchmarkError(
            f"{label} dataset_digest must be a lowercase SHA-256 digest"
        )
    metrics = raw["metrics"]
    if not isinstance(metrics, Mapping) or set(metrics) != set(HIGHER_IS_BETTER):
        raise BenchmarkError(f"{label} metric keys are closed")
    for metric, value in metrics.items():
        _bounded_number(value, f"{label} metric {metric}", maximum=1)
    case_metrics = raw["case_metrics"]
    if not isinstance(case_metrics, Mapping) or set(case_metrics) != (
        set(HIGHER_IS_BETTER) | {"case_ids"}
    ):
        raise BenchmarkError(f"{label} case metric keys are closed")
    case_ids = case_metrics["case_ids"]
    if (
        not isinstance(case_ids, list)
        or not case_ids
        or len(case_ids) > MAX_DATASET_CASES
        or any(
            not isinstance(case_id, str) or not case_id.strip()
            for case_id in case_ids
        )
        or len(case_ids) != len(set(case_ids))
    ):
        raise BenchmarkError(
            f"{label} case metric identities must be unique non-empty strings"
        )
    case_counts: set[int] = set()
    for metric in HIGHER_IS_BETTER:
        values = case_metrics[metric]
        if (
            not isinstance(values, list)
            or not values
        ):
            raise BenchmarkError(
                f"{label} case metric {metric} must be a non-empty list "
                "of finite values from 0 to 1"
            )
        for value in values:
            _bounded_number(
                value, f"{label} case metric {metric}", maximum=1
            )
        case_counts.add(len(values))
        if not math.isclose(
            float(metrics[metric]),
            _mean(values),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise BenchmarkError(
                f"{label} metric {metric} contradicts case metrics"
            )
    if len(case_counts) != 1:
        raise BenchmarkError(
            f"{label} case metric sequences must have equal lengths"
        )
    if next(iter(case_counts)) != len(case_ids):
        raise BenchmarkError(
            f"{label} case metric identities and values must have equal lengths"
        )
    intervals = raw["confidence_intervals"]
    if not isinstance(intervals, Mapping) or set(intervals) != set(
        HIGHER_IS_BETTER
    ):
        raise BenchmarkError(f"{label} confidence interval keys are closed")
    for metric, interval in intervals.items():
        if (
            not isinstance(interval, list)
            or len(interval) != 2
        ):
            raise BenchmarkError(
                f"{label} confidence interval {metric} is invalid"
            )
        normalized_interval = [
            _bounded_number(
                value,
                f"{label} confidence interval {metric}",
                maximum=1,
            )
            for value in interval
        ]
        if normalized_interval[0] > normalized_interval[1]:
            raise BenchmarkError(
                f"{label} confidence interval {metric} is invalid"
            )
    bootstrap = raw["bootstrap"]
    if not isinstance(bootstrap, Mapping) or set(bootstrap) != {
        "seed",
        "samples",
        "confidence",
    }:
        raise BenchmarkError(f"{label} bootstrap keys are closed")
    if type(bootstrap["seed"]) is not int:
        raise BenchmarkError(f"{label} bootstrap seed must be an integer")
    if type(bootstrap["samples"]) is not int or not 1 <= bootstrap["samples"] <= 100_000:
        raise BenchmarkError(f"{label} bootstrap samples are invalid")
    confidence = _bounded_number(
        bootstrap["confidence"], f"{label} bootstrap confidence", maximum=1
    )
    if not 0 < confidence < 1:
        raise BenchmarkError(f"{label} bootstrap confidence is invalid")
    gates = raw["gates"]
    if not isinstance(gates, Mapping) or set(gates) != {
        "must_recall",
        "must_not_return",
        "policy",
        "passed",
    }:
        raise BenchmarkError(f"{label} gate keys are closed")
    for gate in ("must_recall", "must_not_return"):
        value = gates[gate]
        if not isinstance(value, Mapping) or set(value) != {
            "passed",
            "failures",
        }:
            raise BenchmarkError(f"{label} {gate} gate keys are closed")
        if type(value["passed"]) is not bool or not isinstance(
            value["failures"], list
        ):
            raise BenchmarkError(f"{label} {gate} gate is invalid")
        expected_failure_keys = (
            {"case_id", "document_id"}
            if gate == "must_recall"
            else {"case_id", "document_id", "rank"}
        )
        for failure in value["failures"]:
            if not isinstance(failure, Mapping) or set(failure) != expected_failure_keys:
                raise BenchmarkError(f"{label} {gate} failure schema is closed")
            _require_nonempty_string(
                failure["case_id"], f"{label} {gate} failure case_id"
            )
            _require_nonempty_string(
                failure["document_id"],
                f"{label} {gate} failure document_id",
            )
            if gate == "must_not_return" and (
                type(failure["rank"]) is not int or failure["rank"] < 1
            ):
                raise BenchmarkError(
                    f"{label} must_not_return failure rank is invalid"
                )
        if value["passed"] != (not value["failures"]):
            raise BenchmarkError(f"{label} {gate} gate contradicts failures")
    policy = gates["policy"]
    if (
        not isinstance(policy, Mapping)
        or set(policy) != {"passed"}
        or type(policy["passed"]) is not bool
        or type(gates["passed"]) is not bool
    ):
        raise BenchmarkError(f"{label} policy gate is invalid")
    expected_passed = bool(
        gates["must_recall"]["passed"]
        and gates["must_not_return"]["passed"]
        and policy["passed"]
    )
    if gates["passed"] != expected_passed:
        raise BenchmarkError(f"{label} aggregate gate is inconsistent")
    _validate_dimensions(raw["dimensions"])
    return raw


def promotion_eligibility(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply thresholds without resolving or activating a model."""
    baseline = _validate_promotion_report(baseline, "baseline")
    candidate = _validate_promotion_report(candidate, "candidate")
    baseline_dataset = baseline.get("dataset_digest")
    candidate_dataset = candidate.get("dataset_digest")
    if (
        not isinstance(baseline_dataset, str)
        or not re.fullmatch(r"[0-9a-f]{64}", baseline_dataset)
        or not isinstance(candidate_dataset, str)
        or not re.fullmatch(r"[0-9a-f]{64}", candidate_dataset)
        or not hmac.compare_digest(baseline_dataset, candidate_dataset)
    ):
        raise BenchmarkError(
            "promotion baseline and candidate require the same bound dataset digest"
        )
    baseline_envelope = _require_nonempty_string(
        baseline.get("deployment_envelope"),
        "baseline deployment_envelope",
    )
    candidate_envelope = _require_nonempty_string(
        candidate.get("deployment_envelope"),
        "candidate deployment_envelope",
    )
    if baseline_envelope != candidate_envelope:
        raise BenchmarkError(
            "promotion baseline must use the candidate deployment envelope"
        )
    if baseline["bootstrap"] != candidate["bootstrap"]:
        raise BenchmarkError(
            "promotion reports must use identical bootstrap parameters"
        )
    if baseline["case_metrics"]["case_ids"] != candidate["case_metrics"]["case_ids"]:
        raise BenchmarkError(
            "promotion reports must have identical case identity and order"
        )
    baseline_case_count = len(baseline["case_metrics"][HIGHER_IS_BETTER[0]])
    candidate_case_count = len(candidate["case_metrics"][HIGHER_IS_BETTER[0]])
    if baseline_case_count != candidate_case_count:
        raise BenchmarkError(
            "promotion reports must contain the same number of benchmark cases"
        )
    limits = _validate_thresholds(thresholds)
    epsilon = 1e-12
    paired_deltas = {
        metric: [
            float(candidate["case_metrics"][metric][index])
            - float(baseline["case_metrics"][metric][index])
            for index in range(baseline_case_count)
        ]
        for metric in HIGHER_IS_BETTER
    }
    paired_intervals = _bootstrap_intervals(
        paired_deltas,
        seed=baseline["bootstrap"]["seed"],
        samples=baseline["bootstrap"]["samples"],
        confidence=baseline["bootstrap"]["confidence"],
    )
    regressions = [
        metric
        for metric in HIGHER_IS_BETTER
        if paired_intervals[metric][0]
        < -limits["max_retrieval_regression"][metric] - epsilon
    ]
    gains: list[str] = []
    for metric in HIGHER_IS_BETTER:
        lower_bound = paired_intervals[metric][0]
        if (
            lower_bound > epsilon
            and lower_bound + epsilon
            >= limits["meaningful_gain"][metric]
        ):
            gains.append(metric)
    for metric in (
        "latency_ms_p95",
        "direct_cost_usd",
        "peak_memory_mb",
        "model_footprint_mb",
    ):
        delta = baseline["dimensions"][metric] - candidate["dimensions"][metric]
        if (
            delta > epsilon
            and delta + epsilon >= limits["meaningful_gain"][metric]
        ):
            gains.append(metric)
    leakage_policy_safe = bool(
        candidate["gates"]["must_not_return"]["passed"]
        and candidate["gates"]["policy"]["passed"]
    )
    retrieval_gates_passed = bool(
        candidate["gates"]["must_recall"]["passed"]
    )
    readiness_passed = bool(
        candidate["dimensions"]["provider_available"]
        and candidate["dimensions"]["compatible"]
        and candidate["dimensions"]["license_ready"]
    )
    reasons: list[str] = []
    if regressions:
        reasons.append("material retrieval regression")
    if not retrieval_gates_passed:
        reasons.append("retrieval gate failure")
    if not leakage_policy_safe:
        reasons.append("policy or leakage failure")
    if not readiness_passed:
        reasons.append("provider, compatibility, or license gate failure")
    if not gains:
        reasons.append("no meaningful gain")
    eligible = (
        not regressions
        and retrieval_gates_passed
        and leakage_policy_safe
        and readiness_passed
        and bool(gains)
    )
    return {
        "eligible": eligible,
        "no_material_retrieval_regression": not regressions,
        "retrieval_gates_passed": retrieval_gates_passed,
        "no_policy_or_leakage_failure": leakage_policy_safe,
        "readiness_passed": readiness_passed,
        "has_meaningful_gain": bool(gains),
        "meaningful_gains": gains,
        "material_regressions": regressions,
        "paired_delta_confidence_intervals": paired_intervals,
        "reasons": reasons,
    }


def evaluate_benchmark(
    dataset: BenchmarkDataset,
    candidates: Iterable[Mapping[str, Any]],
    *,
    baseline_id: str,
    seed: int,
    bootstrap_samples: int,
    promotion_thresholds: Mapping[str, Any],
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Build a complete deterministic benchmark and promotion report."""
    reports = []
    for index, candidate in enumerate(candidates, start=1):
        if index > MAX_BENCHMARK_CANDIDATES:
            raise BenchmarkError("benchmark candidate limit exceeded")
        reports.append(evaluate_candidate(
            dataset,
            candidate,
            seed=seed,
            bootstrap_samples=bootstrap_samples,
            confidence=confidence,
        ))
    reports.sort(
        key=lambda report: (
            report["deployment_envelope"],
            report["candidate_id"],
        )
    )
    identities = {
        (report["deployment_envelope"], report["candidate_id"])
        for report in reports
    }
    if len(identities) != len(reports):
        raise BenchmarkError(
            "candidate_id values must be unique within each deployment envelope"
        )
    baseline_by_envelope = {
        report["deployment_envelope"]: report
        for report in reports
        if report["candidate_id"] == baseline_id
    }
    if not baseline_by_envelope:
        raise BenchmarkError(f"baseline candidate {baseline_id!r} is missing")
    envelopes = {report["deployment_envelope"] for report in reports}
    missing_baselines = sorted(envelopes - set(baseline_by_envelope))
    if missing_baselines:
        raise BenchmarkError(
            f"baseline candidate {baseline_id!r} is missing for deployment "
            f"envelopes: {', '.join(missing_baselines)}"
        )
    promotions: dict[str, dict[str, Any]] = {}
    for report in reports:
        if report["candidate_id"] == baseline_id:
            continue
        envelope = report["deployment_envelope"]
        promotions.setdefault(envelope, {})[report["candidate_id"]] = (
            promotion_eligibility(
                baseline_by_envelope[envelope],
                report,
                promotion_thresholds,
            )
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_digest": dataset.dataset_digest,
        "baseline_id": baseline_id,
        "bootstrap": {
            "seed": seed,
            "samples": bootstrap_samples,
            "confidence": float(confidence),
        },
        "candidates": reports,
        "pareto_frontiers": pareto_frontier(reports),
        "promotions": promotions,
    }
