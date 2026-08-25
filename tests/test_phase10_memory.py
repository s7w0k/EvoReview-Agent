"""Phase 10 acceptance tests: merged Memory (plan section 14.1-14.3).

Covers the extended MemoryMetadata fields, the retrieval pipeline reranker /
BM25 (14.2), and outcome-to-usefulness feedback wiring (14.3).
"""
import unittest

from evoagent.memory_evolution import (
    MemoryManager,
    MemoryMetadata,
    MemoryOutcomeFeedback,
    MemoryRetriever,
    MemoryUseTracker,
    bm25_relevance,
)
from evoagent.outcome_evolution.outcome import (
    Outcome,
    OutcomeAttribution,
    OutcomeKind,
    RuntimeMetrics,
)


def _outcome(task_id, kind):
    return Outcome(
        task_id=task_id,
        kind=kind,
        tenant_id="tenant-a",
        repository="repo/x",
        risk_level="high",
        attribution=OutcomeAttribution(
            prompt_version="p1", rule_skill_version="r1",
            procedure_version=None, runtime_policy_version=None,
            deployment_lane=None, candidate_id=None),
        metrics=RuntimeMetrics(),
    )


class MemoryMetadataFieldsTest(unittest.TestCase):
    """14.1 -- MemoryMetadata carries all learnable fields, lossless to_dict."""

    def test_fields_present(self):
        md = MemoryMetadata()
        for field_ in ("success_count", "failure_count", "last_used_at",
                       "usefulness_score", "confidence", "source_type",
                       "source_version"):
            self.assertTrue(hasattr(md, field_),
                            msg=f"Missing MemoryMetadata field {field_!r}")

    def test_to_dict_roundtrip(self):
        md = MemoryMetadata(
            success_count=3, failure_count=1, last_used_at=100.0,
            usefulness_score=0.75, confidence=0.9,
            source_type="review", source_version="proc-2")
        d = md.to_dict()
        for key, val in d.items():
            self.assertEqual(getattr(md, key), val,
                             msg=f"to_dict entry {key!r} diverges")


class RetrievalRerankerTest(unittest.TestCase):
    """14.2 -- reranker is invoked after baseline ranking, can reorder."""

    def _manager(self, reranker):
        retriever = MemoryRetriever(reranker=reranker)
        return MemoryManager(retriever=retriever)

    def test_reranker_reorders(self):
        manager = self._manager(reranker=lambda results, q: list(reversed(results)))
        manager.add("a", "alpha security token anda", MemoryMetadata(confidence=1.0))
        manager.add("b", "beta security auth flow", MemoryMetadata(confidence=1.0))
        out = manager.search("security token", limit=2)
        # reversed ranking => lowest by baseline is now first.
        self.assertEqual(out[0].memory_id, "b")

    def test_reranker_receives_query_and_candidates(self):
        seen = {}
        def reranker(results, query):
            seen["query"] = query
            seen["count"] = len(results)
            return results
        manager = self._manager(reranker=reranker)
        manager.add("a", "aws iam policy s3 read", MemoryMetadata())
        manager.add("b", "gcp bucket permission", MemoryMetadata())
        out = manager.search("iam policy", limit=2)
        self.assertEqual(seen["query"], "iam policy")
        # "gcp bucket permission" has no lexical overlap -> filtered out.
        self.assertEqual(seen["count"], 1)
        self.assertEqual(len(out), 1)

    def test_bm25_ranks_covering_doc_higher(self):
        query = "memory retrieval reranker"
        high = bm25_relevance(query, "memory retrieval reranker and ranking")
        low = bm25_relevance(query, "unrelated note about breakfast cereal")
        self.assertGreater(high, low)


class OutcomeFeedbackTest(unittest.TestCase):
    """14.3 -- production outcomes move usefulness up/down."""

    def test_success_marks_helpful(self):
        manager = MemoryManager()
        tracker = MemoryUseTracker()
        feedback = MemoryOutcomeFeedback(manager, tracker)
        manager.add("m1", "lev infra config", MemoryMetadata())
        manager.add("m2", "sandbox rule", MemoryMetadata())
        feedback.note_used("t1", ["m1", "m2"])
        touched = feedback.apply(_outcome("t1", OutcomeKind.TASK_SUCCESS))
        self.assertEqual(sorted(touched), ["m1", "m2"])
        self.assertEqual(manager.get("m1").metadata.success_count, 1)
        self.assertEqual(manager.get("m2").metadata.success_count, 1)
        self.assertEqual(manager.get("m1").metadata.usefulness_score, 0.5)

    def test_rejected_marks_unhelpful(self):
        manager = MemoryManager()
        feedback = MemoryOutcomeFeedback(manager)
        manager.add("m1", "lev infra config", MemoryMetadata())
        feedback.note_used("t1", ["m1"])
        feedback.apply(_outcome("t1", OutcomeKind.FINDING_REJECTED))
        self.assertEqual(manager.get("m1").metadata.failure_count, 1)
        self.assertEqual(manager.get("m1").metadata.usefulness_score, 0.0)

    def test_false_positive_marks_unhelpful(self):
        manager = MemoryManager()
        feedback = MemoryOutcomeFeedback(manager)
        manager.add("m1", "lev infra config", MemoryMetadata())
        feedback.note_used("t1", ["m1"])
        feedback.apply(_outcome("t1", OutcomeKind.FALSE_POSITIVE))
        self.assertEqual(manager.get("m1").metadata.failure_count, 1)

    def test_neutral_outcome_leaves_alone(self):
        manager = MemoryManager()
        feedback = MemoryOutcomeFeedback(manager)
        manager.add("m1", "lev infra config", MemoryMetadata())
        feedback.note_used("t1", ["m1"])
        touched = feedback.apply(_outcome("t1", OutcomeKind.TASK_FAILURE))
        self.assertEqual(touched, [])
        self.assertEqual(manager.get("m1").metadata.success_count, 0)
        self.assertEqual(manager.get("m1").metadata.failure_count, 0)

    def test_untracked_task_noop(self):
        manager = MemoryManager()
        feedback = MemoryOutcomeFeedback(manager)
        manager.add("m1", "lev infra config", MemoryMetadata())
        touched = feedback.apply(_outcome("t1", OutcomeKind.TASK_SUCCESS))
        self.assertEqual(touched, [])

    def test_tracker_cleared_after_apply(self):
        manager = MemoryManager()
        feedback = MemoryOutcomeFeedback(manager)
        manager.add("m1", "lev infra config", MemoryMetadata())
        feedback.note_used("t1", ["m1"])
        feedback.apply(_outcome("t1", OutcomeKind.TASK_SUCCESS))
        self.assertEqual(feedback.apply(_outcome("t1", OutcomeKind.TASK_SUCCESS)), [])
        # success_count changed exactly once, not twice.
        self.assertEqual(manager.get("m1").metadata.success_count, 1)


if __name__ == "__main__":
    unittest.main()