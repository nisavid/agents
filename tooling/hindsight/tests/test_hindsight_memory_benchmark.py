import json
import hashlib
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(LIB))

from hindsight_memory_control_plane.benchmark import (
    BenchmarkDataset,
    BenchmarkError,
    MAX_BOOTSTRAP_CASE_SAMPLES,
    MAX_DATASET_ARTIFACT_BYTES,
    MAX_DATASET_CASES,
    MAX_RETRIEVAL_ENTRIES_PER_CASE,
    _bootstrap_intervals,
    evaluate_benchmark,
    evaluate_candidate,
    load_cases,
    pareto_frontier,
    promotion_eligibility,
)


CASES = [
    {
        "schema_version": 1,
        "case_id": "graded",
        "query": "Which synthetic decisions are relevant?",
        "relevance": {"decision-primary": 3, "decision-secondary": 1},
        "must_recall": ["decision-primary"],
        "must_not_return": ["private-decoy"],
    },
    {
        "schema_version": 1,
        "case_id": "missed",
        "query": "Which synthetic constraint applies?",
        "relevance": {"constraint": 2},
        "must_recall": ["constraint"],
        "must_not_return": ["secret-decoy"],
    },
]
SYNTHETIC_DATASET_DIGEST = (
    "fba84844cc4e75995377d78a9a29fff884c511c05f9646eaf02e49a9a408480e"
)


def cases_digest(cases):
    canonical = json.dumps(
        cases,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def dimensions(**overrides):
    value = {
        "latency_ms_p95": 90.0,
        "direct_cost_usd": 0.02,
        "peak_memory_mb": 700.0,
        "model_footprint_mb": 500.0,
        "provider_available": True,
        "compatible": True,
        "license_ready": True,
    }
    value.update(overrides)
    return value


def candidate(candidate_id="candidate", **overrides):
    value = {
        "candidate_id": candidate_id,
        "deployment_envelope": "local",
        "retrievals": {
            "graded": ["decision-primary", "irrelevant", "decision-secondary"],
            "missed": ["irrelevant"],
        },
        "dimensions": dimensions(),
        "policy_passed": True,
    }
    value.update(overrides)
    return value


def promotion_report(candidate_id="candidate", **overrides):
    value = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "dataset_digest": "d" * 64,
        "deployment_envelope": "local",
        "metrics": {"recall_at_20": 0.9, "ndcg_at_10": 0.8},
        "confidence_intervals": {
            "recall_at_20": [0.85, 0.95],
            "ndcg_at_10": [0.75, 0.85],
        },
        "bootstrap": {"seed": 7, "samples": 200, "confidence": 0.95},
        "dimensions": dimensions(),
        "gates": {
            "passed": True,
            "must_recall": {"passed": True, "failures": []},
            "must_not_return": {"passed": True, "failures": []},
            "policy": {"passed": True},
        },
    }
    value.update(overrides)
    if "case_metrics" not in overrides:
        value["case_metrics"] = {
            "case_ids": ["case-1"],
            **{
                metric: [metric_value]
                for metric, metric_value in value["metrics"].items()
            },
        }
    return value


class BenchmarkTest(unittest.TestCase):
    def test_numeric_surfaces_reject_oversize_values_as_benchmark_errors(self):
        huge = 10**1000
        dataset = BenchmarkDataset(1, tuple(CASES), cases_digest(CASES))
        with self.assertRaises(BenchmarkError):
            evaluate_candidate(
                dataset,
                candidate(
                    dimensions=dimensions(latency_ms_p95=huge)
                ),
                seed=1,
                bootstrap_samples=1,
            )

        baseline = promotion_report("baseline")
        candidate_report = promotion_report("candidate")
        thresholds = {
            "max_retrieval_regression": {
                "recall_at_20": 0.01,
                "ndcg_at_10": 0.01,
            },
            "meaningful_gain": {
                "recall_at_20": 0.02,
                "ndcg_at_10": 0.02,
                "latency_ms_p95": huge,
                "direct_cost_usd": 0.001,
                "peak_memory_mb": 1.0,
                "model_footprint_mb": 1.0,
            },
        }
        with self.assertRaises(BenchmarkError):
            promotion_eligibility(baseline, candidate_report, thresholds)

        oversized_report = deepcopy(candidate_report)
        oversized_report["metrics"]["recall_at_20"] = huge
        oversized_report["case_metrics"]["recall_at_20"] = [huge]
        with self.assertRaises(BenchmarkError):
            promotion_eligibility(baseline, oversized_report, {
                **thresholds,
                "meaningful_gain": {
                    **thresholds["meaningful_gain"],
                    "latency_ms_p95": 1.0,
                },
            })

    def test_candidate_iteration_is_explicitly_bounded(self):
        dataset = BenchmarkDataset(1, tuple(CASES), cases_digest(CASES))
        with patch(
            "hindsight_memory_control_plane.benchmark.MAX_BENCHMARK_CANDIDATES",
            2,
        ), self.assertRaisesRegex(BenchmarkError, "candidate limit"):
            evaluate_benchmark(
                dataset,
                (candidate(str(index)) for index in range(3)),
                baseline_id="0",
                seed=1,
                bootstrap_samples=1,
                promotion_thresholds={},
            )

    def write_cases(self, path, cases=CASES):
        path.write_text(
            "".join(json.dumps(case, sort_keys=True) + "\n" for case in cases),
            encoding="utf-8",
        )

    def test_bootstrap_sample_count_is_bounded(self):
        for samples in (0, 100_001, True):
            with self.subTest(samples=samples), self.assertRaisesRegex(
                BenchmarkError, "integer from 1 to 100000"
            ):
                _bootstrap_intervals(
                    {"recall_at_20": [1.0]},
                    seed=1,
                    samples=samples,
                    confidence=0.95,
                )

    def test_benchmark_resource_work_is_explicitly_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "oversized.jsonl"
            artifact.write_bytes(b" " * (MAX_DATASET_ARTIFACT_BYTES + 1))
            with self.assertRaisesRegex(BenchmarkError, "byte limit"):
                load_cases(artifact, expected_digest="0" * 64)

        cases = tuple(
            {**CASES[0], "case_id": f"case-{index}"}
            for index in range(MAX_DATASET_CASES + 1)
        )
        with self.assertRaisesRegex(BenchmarkError, "cases"):
            BenchmarkDataset(1, cases, "0" * 64)

        dataset = BenchmarkDataset(1, tuple(CASES), cases_digest(CASES))
        oversized_retrievals = candidate()
        oversized_retrievals["retrievals"] = {
            **oversized_retrievals["retrievals"],
            "graded": [
                f"document-{index}"
                for index in range(MAX_RETRIEVAL_ENTRIES_PER_CASE + 1)
            ],
        }
        with self.assertRaisesRegex(BenchmarkError, "entries"):
            evaluate_candidate(dataset, oversized_retrievals, seed=1)

        case_count = MAX_BOOTSTRAP_CASE_SAMPLES // 100_000 + 1
        with self.assertRaisesRegex(BenchmarkError, "work limit"):
            _bootstrap_intervals(
                {"recall_at_20": [1.0] * case_count},
                seed=1,
                samples=100_000,
                confidence=0.95,
            )

    def test_loader_normalizes_path_and_uses_the_shared_bounded_reader(self):
        payload = "".join(
            json.dumps(case, sort_keys=True) + "\n" for case in CASES
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_bytes(payload)
            with patch(
                "hindsight_memory_control_plane.benchmark.read_file_evidence",
                create=True,
                return_value=(payload, "f" * 64),
            ) as reader:
                dataset = load_cases(
                    path, expected_digest=cases_digest(CASES)
                )

        self.assertEqual(dataset.dataset_digest, cases_digest(CASES))
        reader.assert_called_once_with(
            path.resolve(),
            "benchmark dataset artifact",
            max_bytes=MAX_DATASET_ARTIFACT_BYTES,
        )

    def test_loads_schema_versioned_cases_and_digest_binds_canonical_content(
        self,
    ):
        schema_path = ROOT / "config/benchmark-schema.json"
        fixture_path = ROOT / "examples/synthetic-benchmark.jsonl"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            schema["required"],
            [
                "schema_version",
                "case_id",
                "query",
                "relevance",
                "must_recall",
                "must_not_return",
            ],
        )
        self.assertEqual(schema["properties"]["case_id"]["pattern"], ".*\\S.*")
        self.assertEqual(schema["properties"]["query"]["pattern"], ".*\\S.*")
        self.assertEqual(schema["properties"]["must_recall"]["minItems"], 1)
        self.assertEqual(schema["properties"]["must_recall"]["maxItems"], 20)
        self.assertEqual(
            schema["properties"]["relevance"]["propertyNames"]["pattern"],
            ".*\\S.*",
        )
        self.assertIn("executable load_cases validator", schema["$comment"])
        self.assertIn("cross-record invariants", schema["$comment"])

        with self.assertRaises(TypeError):
            load_cases(fixture_path)
        dataset = load_cases(
            fixture_path, expected_digest=SYNTHETIC_DATASET_DIGEST
        )
        self.assertEqual(dataset.schema_version, 1)
        self.assertEqual(len(dataset.cases), 3)
        self.assertEqual(dataset.cases[0]["case_id"], "architecture-decision")
        self.assertEqual(
            dataset.dataset_digest,
            SYNTHETIC_DATASET_DIGEST,
        )
        self.assertEqual(
            load_cases(fixture_path, expected_digest=dataset.dataset_digest),
            dataset,
        )

        with self.assertRaisesRegex(BenchmarkError, "dataset digest mismatch"):
            load_cases(fixture_path, expected_digest="0" * 64)

    def test_loaded_cases_cannot_change_after_their_digest_is_bound(self):
        fixture_path = ROOT / "examples/synthetic-benchmark.jsonl"
        dataset = load_cases(
            fixture_path, expected_digest=SYNTHETIC_DATASET_DIGEST
        )
        bound_digest = dataset.dataset_digest

        with self.assertRaises(TypeError):
            dataset.cases[0]["relevance"]["post-digest-document"] = 3
        with self.assertRaises(TypeError):
            dataset.cases[0]["query"] = "changed after digest"
        with self.assertRaises((AttributeError, TypeError)):
            dataset.cases[0]["must_recall"].append("post-digest-document")

        with self.assertRaisesRegex(BenchmarkError, "dataset digest mismatch"):
            BenchmarkDataset(1, tuple(CASES), "a" * 64)
        with self.assertRaisesRegex(BenchmarkError, "dataset schema_version"):
            BenchmarkDataset(2, tuple(CASES), "a" * 64)
        with self.assertRaisesRegex(BenchmarkError, "relevance"):
            BenchmarkDataset(
                1,
                ({**CASES[0], "relevance": {"decision-primary": 0}},),
                "a" * 64,
            )
        with self.assertRaisesRegex(BenchmarkError, "duplicate case_id"):
            BenchmarkDataset(1, (CASES[0], CASES[0]), "a" * 64)

        directly_constructed = BenchmarkDataset(
            dataset.schema_version, dataset.cases, bound_digest
        )
        with self.assertRaises(TypeError):
            directly_constructed.cases[0]["relevance"][
                "post-digest-document"
            ] = 3

        forged = BenchmarkDataset(
            dataset.schema_version, dataset.cases, bound_digest
        )
        object.__setattr__(forged, "cases", (dataset.cases[0], dataset.cases[0]))
        with self.assertRaisesRegex(BenchmarkError, "duplicate case_id"):
            evaluate_candidate(
                forged,
                {
                    "candidate_id": "forged-dataset",
                    "deployment_envelope": "local",
                    "retrievals": {},
                    "dimensions": dimensions(),
                    "policy_passed": True,
                },
                seed=11,
                bootstrap_samples=10,
            )

        self.assertEqual(dataset.dataset_digest, bound_digest)
        report = evaluate_candidate(
            dataset,
            {
                "candidate_id": "digest-bound",
                "deployment_envelope": "local",
                "retrievals": {
                    "architecture-decision": [
                        "public-decision",
                        "public-context",
                    ],
                    "operational-constraint": ["public-constraint"],
                    "historical-noise": ["public-current-convention"],
                },
                "dimensions": dimensions(),
                "policy_passed": True,
            },
            seed=11,
            bootstrap_samples=10,
        )
        json.dumps(report)
        self.assertEqual(report["dataset_digest"], bound_digest)

    def test_rejects_unknown_schema_invalid_judgments_and_duplicate_cases(self):
        invalid_sets = [
            [{**CASES[0], "schema_version": 2}],
            [{**CASES[0], "relevance": {"decision-primary": 0}}],
            [CASES[0], CASES[0]],
            [{**CASES[0], "surprise": True}],
            [{**CASES[0], "must_recall": ["unjudged"]}],
            [{**CASES[0], "must_not_return": ["decision-primary"]}],
            [{**CASES[0], "case_id": "   "}],
            [{**CASES[0], "query": "\t"}],
            [{**CASES[0], "relevance": {" ": 1}}],
            [{**CASES[0], "must_recall": ["\t"]}],
            [{**CASES[0], "must_recall": []}],
            [{
                **CASES[0],
                "relevance": {f"doc-{index}": 1 for index in range(21)},
                "must_recall": [f"doc-{index}" for index in range(21)],
            }],
            [{**CASES[0], "must_not_return": [" "]}],
        ]
        messages = [
            "schema_version",
            "relevance",
            "duplicate case_id",
            "keys",
            "must_recall",
            "must_not_return",
            "case_id",
            "query",
            "relevance document ID",
            "must_recall must be a list of non-empty strings",
            "must_recall must not be empty",
            "must_recall cannot exceed the top-20 retrieval gate",
            "must_not_return must be a list of non-empty strings",
        ]
        with tempfile.TemporaryDirectory() as directory:
            for index, (cases, message) in enumerate(
                zip(invalid_sets, messages, strict=True)
            ):
                path = Path(directory) / f"invalid-{index}.jsonl"
                self.write_cases(path, cases)
                with self.assertRaisesRegex(BenchmarkError, message):
                    load_cases(path, expected_digest=cases_digest(cases))

    def test_rejects_duplicate_json_keys_with_the_source_line_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate-key.jsonl"
            path.write_text(
                "\n"
                '{"schema_version":1,"case_id":"first","query":"one",'
                '"query":"two","relevance":{"doc":3},'
                '"must_recall":["doc"],"must_not_return":[]}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                BenchmarkError,
                r"benchmark line 2.*duplicate JSON object key: query",
            ):
                load_cases(path, expected_digest="0" * 64)

    def test_jsonl_records_are_split_only_on_literal_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vertical-tab.jsonl"
            path.write_text(
                json.dumps(CASES[0]) + "\v" + json.dumps(CASES[1]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                BenchmarkError, r"benchmark line 1.*not valid JSON"
            ):
                load_cases(path, expected_digest=cases_digest(CASES))

    def test_metrics_bootstrap_and_retrieval_gates_are_exact_and_deterministic(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            self.write_cases(path)
            dataset = load_cases(path, expected_digest=cases_digest(CASES))

        report = evaluate_candidate(
            dataset, candidate(), seed=1701, bootstrap_samples=200
        )
        # Hand-calculated from the literal relevance grades and ranks.
        self.assertEqual(report["metrics"]["recall_at_20"], 0.5)
        self.assertAlmostEqual(
            report["metrics"]["ndcg_at_10"], 0.49142111395336985, places=15
        )
        self.assertEqual(
            report["bootstrap"],
            {"seed": 1701, "samples": 200, "confidence": 0.95},
        )
        self.assertEqual(report["case_metrics"]["case_ids"], ["graded", "missed"])
        self.assertEqual(
            report["confidence_intervals"]["recall_at_20"], [0.0, 1.0]
        )
        self.assertEqual(
            report["confidence_intervals"]["ndcg_at_10"],
            [0.0, 0.9828422279067397],
        )
        self.assertEqual(
            report,
            evaluate_candidate(
                dataset, candidate(), seed=1701, bootstrap_samples=200
            ),
        )
        self.assertEqual(
            report["gates"]["must_recall"]["failures"],
            [{"case_id": "missed", "document_id": "constraint"}],
        )
        self.assertTrue(report["gates"]["must_not_return"]["passed"])
        self.assertFalse(report["gates"]["passed"])

        leaking = candidate(
            retrievals={
                "graded": [
                    "decision-primary",
                    "private-decoy",
                    "decision-secondary",
                ],
                "missed": ["constraint", "secret-decoy"],
            }
        )
        leaked = evaluate_candidate(
            dataset, leaking, seed=7, bootstrap_samples=20
        )
        self.assertEqual(
            leaked["gates"]["must_not_return"]["failures"],
            [
                {
                    "case_id": "graded",
                    "document_id": "private-decoy",
                    "rank": 2,
                },
                {"case_id": "missed", "document_id": "secret-decoy", "rank": 2},
            ],
        )
        self.assertFalse(leaked["gates"]["passed"])

    def test_pareto_frontier_is_per_envelope_and_excludes_unready_candidates(
        self,
    ):
        def report(
            candidate_id,
            recall,
            ndcg,
            dims,
            envelope="local",
            gates_passed=True,
        ):
            gates = {
                "passed": gates_passed,
                "must_recall": {
                    "passed": gates_passed,
                    "failures": []
                    if gates_passed
                    else [{"case_id": "case", "document_id": "required"}],
                },
                "must_not_return": {"passed": True, "failures": []},
                "policy": {"passed": True},
            }
            return promotion_report(
                candidate_id,
                deployment_envelope=envelope,
                metrics={"recall_at_20": recall, "ndcg_at_10": ndcg},
                dimensions=dims,
                gates=gates,
            )

        reports = [
            report(
                "balanced",
                0.9,
                0.9,
                dimensions(latency_ms_p95=80, direct_cost_usd=0.02),
            ),
            report(
                "dominated",
                0.8,
                0.8,
                dimensions(latency_ms_p95=100, direct_cost_usd=0.03),
            ),
            report(
                "fast",
                0.8,
                0.8,
                dimensions(latency_ms_p95=40, direct_cost_usd=0.03),
            ),
            report(
                "license-blocked", 1.0, 1.0, dimensions(license_ready=False)
            ),
            report(
                "hosted",
                0.7,
                0.7,
                dimensions(latency_ms_p95=10),
                envelope="hosted",
            ),
        ]
        self.assertEqual(
            pareto_frontier(reports),
            {"hosted": ["hosted"], "local": ["balanced", "fast"]},
        )
        with self.assertRaisesRegex(BenchmarkError, "duplicate candidate_id"):
            pareto_frontier([reports[0], deepcopy(reports[0])])
        malformed = deepcopy(reports[0])
        malformed["case_metrics"]["recall_at_20"] = []
        with self.assertRaisesRegex(BenchmarkError, "case metric"):
            pareto_frontier([malformed])

    def test_pareto_frontier_requires_one_dataset_digest_per_envelope(self):
        reports = [
            promotion_report("first", dataset_digest="d" * 64),
            promotion_report("second", dataset_digest="e" * 64),
        ]

        with self.assertRaisesRegex(BenchmarkError, "dataset_digest"):
            pareto_frontier(reports)

    def test_pareto_and_promotion_case_counts_are_bounded(self):
        with patch(
            "hindsight_memory_control_plane.benchmark.MAX_BENCHMARK_CANDIDATES",
            1,
        ), self.assertRaisesRegex(BenchmarkError, "candidate count"):
            pareto_frontier(
                [promotion_report("first"), promotion_report("second")]
            )

        oversized = promotion_report("oversized")
        oversized["case_metrics"]["case_ids"] = ["a", "b"]
        for metric in ("recall_at_20", "ndcg_at_10"):
            oversized["case_metrics"][metric] = [
                oversized["metrics"][metric],
                oversized["metrics"][metric],
            ]
        with patch(
            "hindsight_memory_control_plane.benchmark.MAX_DATASET_CASES", 1
        ), self.assertRaisesRegex(BenchmarkError, "case metric"):
            pareto_frontier([oversized])

    def test_pareto_candidate_order_does_not_serialize_candidates(self):
        reports = [
            promotion_report(
                "second",
                metrics={"recall_at_20": 0.8, "ndcg_at_10": 0.9},
            ),
            promotion_report(
                "first",
                metrics={"recall_at_20": 0.9, "ndcg_at_10": 0.8},
            ),
        ]

        with patch(
            "hindsight_memory_control_plane.canonical.canonical_bytes",
            side_effect=AssertionError("candidate sorting serialized a report"),
        ):
            self.assertEqual(
                pareto_frontier(reports), {"local": ["first", "second"]}
            )

    def test_promotion_retrieval_decisions_use_paired_case_bootstrap(self):
        baseline = promotion_report(
            "baseline",
            metrics={"recall_at_20": 0.5, "ndcg_at_10": 0.5},
            case_metrics={
                "case_ids": ["a", "b", "c", "d"],
                "recall_at_20": [0.5, 0.5, 0.5, 0.5],
                "ndcg_at_10": [0.5, 0.5, 0.5, 0.5],
            },
        )
        candidate_report = promotion_report(
            "candidate",
            metrics={"recall_at_20": 0.6, "ndcg_at_10": 0.5},
            case_metrics={
                "case_ids": ["a", "b", "c", "d"],
                "recall_at_20": [0.9, 0.9, 0.1, 0.5],
                "ndcg_at_10": [0.5, 0.5, 0.5, 0.5],
            },
        )
        thresholds = {
            "max_retrieval_regression": {
                "recall_at_20": 0.2,
                "ndcg_at_10": 0.2,
            },
            "meaningful_gain": {
                "recall_at_20": 0.05,
                "ndcg_at_10": 0.05,
                "latency_ms_p95": 1000.0,
                "direct_cost_usd": 1000.0,
                "peak_memory_mb": 1000.0,
                "model_footprint_mb": 1000.0,
            },
        }
        decision = promotion_eligibility(
            baseline, candidate_report, thresholds
        )
        self.assertLess(
            decision["paired_delta_confidence_intervals"]["recall_at_20"][0],
            thresholds["meaningful_gain"]["recall_at_20"],
        )
        self.assertFalse(decision["has_meaningful_gain"])
        self.assertFalse(decision["eligible"])

        reordered = deepcopy(candidate_report)
        reordered["case_metrics"]["case_ids"] = ["b", "a", "c", "d"]
        with self.assertRaisesRegex(BenchmarkError, "case identity and order"):
            promotion_eligibility(baseline, reordered, thresholds)

    def test_promotion_regression_uses_the_lower_paired_bound(self):
        baseline = promotion_report(
            "baseline",
            metrics={"recall_at_20": 0.5, "ndcg_at_10": 0.5},
            case_metrics={
                "case_ids": ["a", "b"],
                "recall_at_20": [0.5, 0.5],
                "ndcg_at_10": [0.5, 0.5],
            },
        )
        candidate_report = promotion_report(
            "candidate",
            metrics={"recall_at_20": 0.4, "ndcg_at_10": 0.5},
            case_metrics={
                "case_ids": ["a", "b"],
                "recall_at_20": [0.3, 0.5],
                "ndcg_at_10": [0.5, 0.5],
            },
            dimensions=dimensions(latency_ms_p95=70),
        )
        thresholds = {
            "max_retrieval_regression": {
                "recall_at_20": 0.1,
                "ndcg_at_10": 0.1,
            },
            "meaningful_gain": {
                "recall_at_20": 1.0,
                "ndcg_at_10": 1.0,
                "latency_ms_p95": 10.0,
                "direct_cost_usd": 1.0,
                "peak_memory_mb": 1_000.0,
                "model_footprint_mb": 1_000.0,
            },
        }

        decision = promotion_eligibility(
            baseline, candidate_report, thresholds
        )

        interval = decision["paired_delta_confidence_intervals"]["recall_at_20"]
        self.assertLess(interval[0], -0.1)
        self.assertGreaterEqual(interval[1], -0.1)
        self.assertFalse(decision["no_material_retrieval_regression"])
        self.assertFalse(decision["eligible"])

    def test_promotion_requires_safety_readiness_and_a_meaningful_gain(
        self,
    ):
        base = promotion_report(
            "base",
            metrics={"recall_at_20": 0.90, "ndcg_at_10": 0.80},
            dimensions=dimensions(
                latency_ms_p95=100, direct_cost_usd=0.02, peak_memory_mb=800
            ),
        )
        improved = promotion_report(
            "improved",
            metrics={"recall_at_20": 0.89, "ndcg_at_10": 0.80},
            dimensions=dimensions(
                latency_ms_p95=75, direct_cost_usd=0.02, peak_memory_mb=800
            ),
        )
        thresholds = {
            "max_retrieval_regression": {
                "recall_at_20": 0.02,
                "ndcg_at_10": 0.01,
            },
            "meaningful_gain": {
                "recall_at_20": 0.02,
                "ndcg_at_10": 0.02,
                "latency_ms_p95": 10.0,
                "direct_cost_usd": 0.005,
                "peak_memory_mb": 100.0,
                "model_footprint_mb": 100.0,
            },
        }
        decision = promotion_eligibility(base, improved, thresholds)
        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["meaningful_gains"], ["latency_ms_p95"])

        with self.assertRaisesRegex(BenchmarkError, "dataset digest"):
            promotion_eligibility(
                base, {**improved, "dataset_digest": "e" * 64}, thresholds
            )

        malformed_reports = (
            lambda value: value.update({"unknown": True}),
            lambda value: value["metrics"].update({"recall_at_20": True}),
            lambda value: value["metrics"].update(
                {"recall_at_20": float("nan")}
            ),
            lambda value: value["dimensions"].update(
                {"latency_ms_p95": -1}
            ),
            lambda value: value["dimensions"].update(
                {"provider_available": 1}
            ),
            lambda value: value["confidence_intervals"].update(
                {"recall_at_20": [0.9, 0.1]}
            ),
            lambda value: value["bootstrap"].update({"samples": True}),
            lambda value: value["gates"].update({"passed": 1}),
        )
        for mutate in malformed_reports:
            malformed = deepcopy(improved)
            mutate(malformed)
            with self.subTest(report=malformed), self.assertRaises(
                BenchmarkError
            ):
                promotion_eligibility(base, malformed, thresholds)

        no_gain = {
            **improved,
            "candidate_id": "no-gain",
            "dimensions": dimensions(latency_ms_p95=95, peak_memory_mb=800),
        }
        self.assertFalse(
            promotion_eligibility(base, no_gain, thresholds)["eligible"]
        )
        regression = {
            **improved,
            "candidate_id": "regression",
            "metrics": {"recall_at_20": 0.87, "ndcg_at_10": 0.80},
            "case_metrics": {
                "case_ids": ["case-1"],
                "recall_at_20": [0.87],
                "ndcg_at_10": [0.80],
            },
        }
        self.assertFalse(
            promotion_eligibility(base, regression, thresholds)["eligible"]
        )
        leak = {
            **improved,
            "candidate_id": "leak",
            "gates": {
                "passed": False,
                "must_recall": {"passed": True, "failures": []},
                "must_not_return": {
                    "passed": False,
                    "failures": [
                        {"case_id": "case", "document_id": "private", "rank": 1}
                    ],
                },
                "policy": {"passed": True},
            },
        }
        self.assertFalse(
            promotion_eligibility(base, leak, thresholds)["eligible"]
        )
        policy_only = {
            **improved,
            "candidate_id": "policy-only",
            "gates": {
                "passed": False,
                "must_recall": {"passed": True, "failures": []},
                "must_not_return": {"passed": True, "failures": []},
                "policy": {"passed": False},
            },
        }
        policy_decision = promotion_eligibility(
            base, policy_only, thresholds
        )
        self.assertTrue(policy_decision["retrieval_gates_passed"])
        self.assertFalse(policy_decision["no_policy_or_leakage_failure"])
        self.assertNotIn("retrieval gate failure", policy_decision["reasons"])
        blocked = {
            **improved,
            "candidate_id": "blocked",
            "dimensions": dimensions(latency_ms_p95=75, compatible=False),
        }
        self.assertFalse(
            promotion_eligibility(base, blocked, thresholds)["eligible"]
        )
        footprint_gain = {
            **base,
            "candidate_id": "footprint-gain",
            "dimensions": dimensions(
                latency_ms_p95=100,
                direct_cost_usd=0.02,
                peak_memory_mb=800,
                model_footprint_mb=350,
            ),
        }
        self.assertEqual(
            promotion_eligibility(base, footprint_gain, thresholds)[
                "meaningful_gains"
            ],
            ["model_footprint_mb"],
        )
        self.assertTrue(
            promotion_eligibility(base, footprint_gain, thresholds)["eligible"]
        )

        zero_thresholds = {
            "max_retrieval_regression": {
                "recall_at_20": 0.0,
                "ndcg_at_10": 0.0,
            },
            "meaningful_gain": {
                "recall_at_20": 0.0,
                "ndcg_at_10": 0.0,
                "latency_ms_p95": 0.0,
                "direct_cost_usd": 0.0,
                "peak_memory_mb": 0.0,
                "model_footprint_mb": 0.0,
            },
        }
        unchanged = {**base, "candidate_id": "unchanged"}
        self.assertFalse(
            promotion_eligibility(base, unchanged, zero_thresholds)["eligible"]
        )

        with self.assertRaisesRegex(BenchmarkError, "deployment envelope"):
            promotion_eligibility(
                base,
                {**improved, "deployment_envelope": "hosted"},
                thresholds,
            )

    def test_promotions_use_a_baseline_from_each_deployment_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            self.write_cases(path)
            dataset = load_cases(path, expected_digest=cases_digest(CASES))
        retrievals = {
            "graded": ["decision-primary", "decision-secondary"],
            "missed": ["constraint"],
        }
        candidates = [
            candidate("base", retrievals=retrievals),
            candidate("faster-local", retrievals=retrievals, dimensions=dimensions(latency_ms_p95=70)),
            candidate("base", deployment_envelope="hosted", retrievals=retrievals, dimensions=dimensions(direct_cost_usd=0.02)),
            candidate("cheaper-hosted", deployment_envelope="hosted", retrievals=retrievals, dimensions=dimensions(direct_cost_usd=0.01)),
        ]
        thresholds = {
            "max_retrieval_regression": {"recall_at_20": 0.02, "ndcg_at_10": 0.02},
            "meaningful_gain": {
                "recall_at_20": 0.02,
                "ndcg_at_10": 0.02,
                "latency_ms_p95": 10.0,
                "direct_cost_usd": 0.005,
                "peak_memory_mb": 100.0,
                "model_footprint_mb": 100.0,
            },
        }
        report = evaluate_benchmark(
            dataset,
            candidates,
            baseline_id="base",
            seed=7,
            bootstrap_samples=20,
            promotion_thresholds=thresholds,
        )
        self.assertTrue(
            report["promotions"]["local"]["faster-local"]["eligible"]
        )
        self.assertTrue(
            report["promotions"]["hosted"]["cheaper-hosted"]["eligible"]
        )

        with self.assertRaisesRegex(BenchmarkError, "baseline candidate"):
            evaluate_benchmark(
                dataset,
                [candidates[0], candidates[3]],
                baseline_id="base",
                seed=7,
                bootstrap_samples=20,
                promotion_thresholds=thresholds,
            )

    def test_promotion_ids_are_unique_only_within_their_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            self.write_cases(path)
            dataset = load_cases(path, expected_digest=cases_digest(CASES))
        retrievals = {
            "graded": ["decision-primary", "decision-secondary"],
            "missed": ["constraint"],
        }
        candidates = [
            candidate("base", retrievals=retrievals),
            candidate(
                "faster",
                retrievals=retrievals,
                dimensions=dimensions(latency_ms_p95=70),
            ),
            candidate(
                "base",
                deployment_envelope="hosted",
                retrievals=retrievals,
            ),
            candidate(
                "faster",
                deployment_envelope="hosted",
                retrievals=retrievals,
                dimensions=dimensions(latency_ms_p95=60),
            ),
        ]
        thresholds = {
            "max_retrieval_regression": {
                "recall_at_20": 0.02,
                "ndcg_at_10": 0.02,
            },
            "meaningful_gain": {
                "recall_at_20": 0.02,
                "ndcg_at_10": 0.02,
                "latency_ms_p95": 10.0,
                "direct_cost_usd": 0.005,
                "peak_memory_mb": 100.0,
                "model_footprint_mb": 100.0,
            },
        }

        try:
            report = evaluate_benchmark(
                dataset,
                candidates,
                baseline_id="base",
                seed=7,
                bootstrap_samples=20,
                promotion_thresholds=thresholds,
            )
        except BenchmarkError as error:
            self.fail(f"cross-envelope promotion ID was rejected: {error}")

        self.assertEqual(set(report["promotions"]), {"hosted", "local"})
        self.assertTrue(report["promotions"]["local"]["faster"]["eligible"])
        self.assertTrue(report["promotions"]["hosted"]["faster"]["eligible"])

    def test_full_evaluation_records_seed_dimensions_frontier_and_promotions(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            self.write_cases(path)
            dataset = load_cases(path, expected_digest=cases_digest(CASES))
        base = candidate(
            "base",
            retrievals={
                "graded": ["decision-primary", "decision-secondary"],
                "missed": ["constraint"],
            },
            dimensions=dimensions(latency_ms_p95=100),
        )
        faster = candidate(
            "faster",
            retrievals={
                "graded": ["decision-primary", "decision-secondary"],
                "missed": ["constraint"],
            },
            dimensions=dimensions(latency_ms_p95=70),
        )
        report = evaluate_benchmark(
            dataset,
            [base, faster],
            baseline_id="base",
            seed=42,
            bootstrap_samples=50,
            promotion_thresholds={
                "max_retrieval_regression": {
                    "recall_at_20": 0.0,
                    "ndcg_at_10": 0.0,
                },
                "meaningful_gain": {
                    "recall_at_20": 0.01,
                    "ndcg_at_10": 0.01,
                    "latency_ms_p95": 10,
                    "direct_cost_usd": 0.001,
                    "peak_memory_mb": 10,
                    "model_footprint_mb": 10,
                },
            },
        )
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["bootstrap"]["seed"], 42)
        self.assertEqual(report["pareto_frontiers"], {"local": ["faster"]})
        self.assertTrue(report["promotions"]["local"]["faster"]["eligible"])
        self.assertEqual(
            report["candidates"][1]["dimensions"]["provider_available"], True
        )


if __name__ == "__main__":
    unittest.main()
