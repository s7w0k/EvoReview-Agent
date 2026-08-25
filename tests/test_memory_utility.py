"""Tests for memory usefulness + retrieval (plan section 11)."""
import unittest

from evoagent.memory_evolution.manager import MemoryManager
from evoagent.memory_evolution.retrieval import MemoryRetriever
from evoagent.memory_evolution.utility import (
    MemoryMetadata,
    compute_usefulness,
    memory_score,
    record_helpful,
    record_unhelpful,
)


class MemoryUtilityTest(unittest.TestCase):

    def test_usefulness_formula(self):
        # success / (success + failure + 1)
        self.assertAlmostEqual(compute_usefulness(2, 0), 2 / 3)
        self.assertAlmostEqual(compute_usefulness(0, 0), 0.0)
        self.assertAlmostEqual(compute_usefulness(1, 1), 1 / 3)

    def test_memory_score_product(self):
        self.assertAlmostEqual(memory_score(0.5, 0.5, 0.8, 1.0), 0.2)

    def test_helpful_raises_usefulness(self):
        meta = MemoryMetadata()
        record_helpful(meta)
        self.assertEqual(meta.success_count, 1)
        self.assertGreater(meta.usefulness_score, compute_usefulness(0, 0))

    def test_unhelpful_lowers_usefulness(self):
        meta = MemoryMetadata(success_count=5)
        before = compute_usefulness(5, 0)
        record_unhelpful(meta)
        self.assertLess(meta.usefulness_score, before)


class MemoryRetrievalTest(unittest.TestCase):

    def test_ranks_by_usefulness(self):
        manager = MemoryManager(MemoryRetriever())
        high = manager.add("high", "uses authorization token parser to find sink",
                           MemoryMetadata(success_count=10, confidence=1.0))
        low = manager.add("low", "contains a corporate policy document about auth",
                          MemoryMetadata(success_count=0, confidence=1.0))
        results = manager.search("authorization token")
        self.assertTrue(results)
        # High-usefulness entry must be ranked above the low one.
        self.assertEqual(results[0].memory_id, "high")

    def test_metadata_filter_by_source_type(self):
        manager = MemoryManager()
        manager.add("a", "python os usage", MemoryMetadata(source_type="rule"))
        manager.add("b", "python os usage", MemoryMetadata(source_type="procedure"))
        results = manager.search(
            "python os", source_type="procedure")
        self.assertEqual([r.memory_id for r in results], ["b"])

    def test_confidence_filter(self):
        retriever = MemoryRetriever()
        candidates = [("m", "shared os token text",
                       MemoryMetadata(confidence=0.5, usefulness_score=1.0))]
        # min_confidence 0.9 excludes the 0.5-confidence entry
        self.assertEqual(retriever.retrieve("os token", candidates,
                                            min_confidence=0.9), [])

    def test_evolution_feedback_changes_rank(self):
        manager = MemoryManager()
        manager.add("m", "found callsite of authorize in transaction service",
                    MemoryMetadata(confidence=1.0))
        results = manager.search("authorize callsite")
        self.assertEqual(results[0].memory_id, "m")

        manager.confirm_unhelpful("m")
        manager.confirm_unhelpful("m")
        manager.confirm_unhelpful("m")
        after = manager.search("authorize callsite")
        # Usefulness collapse lowers relevance-weighted score but is still > 0.
        self.assertLess(after[0].score, results[0].score)


if __name__ == "__main__":
    unittest.main()