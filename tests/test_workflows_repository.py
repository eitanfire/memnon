import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.repository import FirestoreWorkflowRepository


class FirestoreWorkflowRepositoryTests(unittest.TestCase):
    def test_update_capture_threading_replaces_threading_map(self):
        doc_ref = Mock()
        capture_collection = Mock()
        capture_collection.document.return_value = doc_ref

        user_doc = Mock()
        user_doc.collection.return_value = capture_collection

        users_collection = Mock()
        users_collection.document.return_value = user_doc

        db = Mock()
        db.collection.return_value = users_collection

        repo = FirestoreWorkflowRepository(db)

        repo.update_capture_threading(
            "user-1",
            "cap-1",
            {
                "context_decision": "kept_separate",
                "context_decision_at": "2026-06-30T05:00:00Z",
            },
        )

        doc_ref.update.assert_called_once()
        payload = doc_ref.update.call_args.args[0]
        self.assertEqual(
            payload["threading"],
            {
                "context_decision": "kept_separate",
                "context_decision_at": "2026-06-30T05:00:00Z",
            },
        )
        self.assertIn("updated_at", payload)
        doc_ref.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
