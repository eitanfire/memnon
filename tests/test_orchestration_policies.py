import unittest
from unittest.mock import patch

from src.orchestration.models import AnalysisResult, SourceEvent
from src.orchestration.policies import select_workflow_jobs


class OrchestrationPolicyTests(unittest.TestCase):
    def build_event(self, workflow: str = "professional", lane: str = "batch") -> SourceEvent:
        return SourceEvent(
            source_event_id="evt-1",
            lane=lane,
            workflow=workflow,
            routing_reason="voice_label",
            title="Credible recap",
            transcript="At BoulderJS, Kyle from Credible asked me to follow up with James.",
            transcript_path="/tmp/transcript.txt",
            transcript_preview="At BoulderJS...",
            note_path="/tmp/note.md",
            archived_audio_path="/tmp/audio.m4a",
            metadata_path="/tmp/metadata.json",
            processed_at="2026-06-26T10:00:00-06:00",
            summary="summary",
            action_items=[],
            suggested_tags=[],
        )

    def test_boulderjs_and_professional_rules_force_jobs_even_if_llm_is_low_confidence(self):
        event = self.build_event()
        analysis = AnalysisResult(
            event_type="boulderjs_demo",
            named_people=["Kyle", "James"],
            named_orgs=["Credible", "BoulderJS"],
            commitments=["follow up with James"],
            follow_up_requests=["follow up with James"],
            product_feedback=[],
            research_signals=[],
            publishable_angles=["messy data to trusted answers"],
            reflection_signals=[],
            professional_signals=["professional"],
        )

        llm_output = {
            "boulderjs_recap_packet": {"confidence": 0.21, "reason": "weak signal"},
            "follow_up_bundle": {"confidence": 0.84, "reason": "explicit follow-up request"},
            "research_note": {"confidence": 0.55, "reason": "product feedback adjacent"},
        }

        jobs = select_workflow_jobs(event, analysis, llm_output)
        jobs_by_type = {job.workflow_type: job for job in jobs}

        self.assertTrue(jobs_by_type["boulderjs_recap_packet"].forced_by_rule)
        self.assertEqual(jobs_by_type["boulderjs_recap_packet"].review_priority, "high")
        self.assertEqual(jobs_by_type["professional_note_bundle"].status, "ready")
        self.assertEqual(jobs_by_type["follow_up_bundle"].needs_review, False)
        self.assertEqual(jobs_by_type["research_note"].needs_review, True)

    def test_follow_up_bundle_is_suppressed_without_people_orgs_or_commitments(self):
        event = self.build_event()
        analysis = AnalysisResult(event_type="private_reflection")
        jobs = select_workflow_jobs(
            event,
            analysis,
            {"follow_up_bundle": {"confidence": 0.9, "reason": "hallucinated"}},
        )
        self.assertNotIn("follow_up_bundle", {job.workflow_type for job in jobs})

    def test_out_of_scope_llm_workflow_keys_are_rejected(self):
        event = self.build_event()
        analysis = AnalysisResult(
            event_type="professional_note",
            named_people=["Kyle"],
            named_orgs=["Credible"],
            commitments=["follow up with James"],
        )

        jobs = select_workflow_jobs(
            event,
            analysis,
            {
                "follow_up_bundle": {"confidence": 0.9, "reason": "explicit ask"},
                "unknown_bundle": {"confidence": 0.95, "reason": "out of scope"},
            },
        )

        self.assertEqual(
            {job.workflow_type for job in jobs},
            {"professional_note_bundle", "follow_up_bundle"},
        )

    def test_suppressors_take_precedence_over_hard_rules(self):
        event = self.build_event()
        analysis = AnalysisResult(event_type="boulderjs_demo")

        with patch(
            "src.orchestration.policies.apply_suppressors",
            return_value=["boulderjs_recap_packet"],
        ):
            jobs = select_workflow_jobs(event, analysis, {})

        self.assertNotIn("boulderjs_recap_packet", {job.workflow_type for job in jobs})


if __name__ == "__main__":
    unittest.main()
