from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contentflow.metrics import build_recap
from contentflow.providers import MockProvider
from contentflow.workflow import ContentMarketingWorkflow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.workflow = ContentMarketingWorkflow(
            workspace=self.workspace,
            provider=MockProvider(),
        )
        self.brief = json.loads(
            (PROJECT_ROOT / "examples" / "brief.json").read_text(
                encoding="utf-8"
            )
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_workflow_is_offline_and_reviewed(self):
        result = self.workflow.run(self.brief, PROJECT_ROOT / "knowledge")
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(len(result["contents"]), 3)
        self.assertEqual(len(result["publish_queue"]), 3)
        for item in result["contents"]:
            self.assertEqual(item["status"], "ready_for_human_review")
            self.assertTrue(item["review"]["passed"])
            self.assertIn(self.brief["product_name"], item["body"])
            for forbidden in self.brief["forbidden_phrases"]:
                self.assertNotIn(forbidden, item["body"])
        self.assertTrue(Path(result["output_path"]).exists())

    def test_knowledge_search_returns_sources(self):
        indexed = self.workflow.rebuild_knowledge(PROJECT_ROOT / "knowledge")
        self.assertGreater(indexed, 0)
        results = self.workflow.index.search("北京 夜游 路线", limit=2)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.source.endswith(".md") for item in results))

    def test_metrics_recap_calculates_rates(self):
        rows = json.loads(
            (PROJECT_ROOT / "examples" / "metrics.json").read_text(
                encoding="utf-8"
            )
        )
        recap = build_recap(rows)
        self.assertIn("xiaohongshu", recap["platforms"])
        self.assertGreater(
            recap["platforms"]["xiaohongshu"]["engagement_rate"], 0
        )


if __name__ == "__main__":
    unittest.main()
