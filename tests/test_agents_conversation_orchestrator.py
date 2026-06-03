from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus
from stormready_v3.agents.conversation_orchestrator import ConversationOrchestratorAgent
from stormready_v3.agents.policy_loader import load_policy


class FakeProvider:
    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def structured_json_call(self, *, system_prompt, user_prompt, max_output_tokens=800):
        del system_prompt, user_prompt, max_output_tokens
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.response


def _ctx(payload: dict[str, Any]) -> AgentContext:
    return AgentContext(
        role=AgentRole.CONVERSATION_ORCHESTRATOR,
        operator_id="op1",
        run_id="run_c",
        triggered_at=datetime.now(UTC),
        payload=payload,
    )


def _base_payload() -> dict[str, Any]:
    return {
        "operator_message": "How's dinner looking tonight?",
        "current_state_digest": {
            "reference_date": "2026-04-14",
            "headline_forecast": {"expected": 112, "low": 98, "high": 126, "confidence": "medium"},
            "near_horizon": [{"service_date": "2026-04-15", "expected": 108}],
            "pending_action": {"kind": "submit_actual", "prompt": "Submit last night's covers"},
            "current_uncertainty": "Weather widening Thursday band.",
            "active_signals_summary": ["Rain risk up for Thursday dinner"],
            "disclaimers": ["Learning is early — patterns may shift."],
        },
        "temporal_digest": {
            "conversation_state": "active",
            "recent_misses": [{"service_date": "2026-04-11", "err_pct": -0.22}],
            "active_hypotheses": [
                {"hypothesis_key": "k1", "proposition": "Rain removes patio covers", "confidence": "medium"}
            ],
            "operator_facts": [],
        },
        "digest_staleness": {"current_state_age_seconds": 60, "source_hash_match": True},
        "recent_turns": [],
        "tool_results": [],
    }


class ConversationOrchestratorAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(AgentRole.CONVERSATION_ORCHESTRATOR)

    def test_grounded_reply_passes_through(self) -> None:
        provider = FakeProvider(response={
            "text": "Tonight looks steady at about 112 covers. Rain may widen the band.",
            "tool_calls": [],
            "suggested_messages": ["Show me last Thursday", "Any open questions?"],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        out = result.outputs[0]
        self.assertIn("112", out["text"])
        self.assertEqual(len(out["suggested_messages"]), 2)

    def test_ungrounded_number_sentence_dropped(self) -> None:
        provider = FakeProvider(response={
            "text": "Tonight looks steady at about 112 covers. We had 847 last Friday. Rain may widen the band.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        text = result.outputs[0]["text"]
        self.assertIn("112", text)
        self.assertNotIn("847", text)
        self.assertIn("Rain may widen", text)

    def test_banned_vocab_in_text_scrubs_to_fallback(self) -> None:
        provider = FakeProvider(response={
            "text": "The brooklyn_delta regime is driving things up.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        text = result.outputs[0]["text"]
        self.assertNotIn("brooklyn_delta", text.lower())
        self.assertNotIn("regime", text.lower())
        # Should fall back to quoting the headline
        self.assertIn("112", text)

    def test_tool_calls_are_parsed(self) -> None:
        provider = FakeProvider(response={
            "text": "Let me pull the breakdown for that night.",
            "tool_calls": [
                {"name": "query_forecast_detail", "arguments": {"service_date": "2026-04-11"}}
            ],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        out = result.outputs[0]
        self.assertEqual(len(out["tool_calls"]), 1)
        self.assertEqual(out["tool_calls"][0]["name"], "query_forecast_detail")
        self.assertEqual(out["tool_calls"][0]["arguments"]["service_date"], "2026-04-11")

    def test_tool_name_alias_is_parsed(self) -> None:
        provider = FakeProvider(response={
            "text": "I saved that.",
            "tool_calls": [{"tool_name": "check_readiness", "arguments": {}}],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        out = result.outputs[0]
        self.assertEqual(out["tool_calls"][0]["name"], "check_readiness")

    def test_obvious_service_note_adds_capture_note_tool_call(self) -> None:
        payload = _base_payload()
        payload["operator_message"] = "We had a private buyout last Friday that filled the room."
        provider = FakeProvider(response={
            "text": "I recorded that note.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        out = result.outputs[0]
        self.assertEqual(out["tool_calls"][0]["name"], "capture_note")
        self.assertEqual(out["tool_calls"][0]["arguments"]["service_date"], "2026-04-10")
        self.assertEqual(out["tool_calls"][0]["arguments"]["service_state"], "private_event_or_buyout")

    def test_setup_readiness_question_adds_check_readiness_tool_call(self) -> None:
        payload = _base_payload()
        payload["operator_message"] = "Am I ready to forecast?"
        payload["current_state_digest"]["phase"] = "setup"
        provider = FakeProvider(response={
            "text": "I will check that.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        out = result.outputs[0]
        self.assertEqual(out["tool_calls"][0]["name"], "check_readiness")

    def test_uploaded_file_adds_interpret_upload_tool_call(self) -> None:
        payload = _base_payload()
        payload["current_state_digest"]["phase"] = "enrichment"
        payload["uploaded_file"] = {
            "headers": ["service_date", "covers"],
            "sample_rows": [{"service_date": "2026-04-10", "covers": 120}],
        }
        provider = FakeProvider(response={
            "text": "I can review that.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        out = result.outputs[0]
        self.assertEqual(out["tool_calls"][0]["name"], "interpret_upload")
        self.assertEqual(out["tool_calls"][0]["arguments"]["headers"], ["service_date", "covers"])

    def test_malformed_tool_call_dropped(self) -> None:
        provider = FakeProvider(response={
            "text": "OK.",
            "tool_calls": ["not a dict", {"no_name": True}, {"name": "query_forecast_detail"}],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        out = result.outputs[0]
        self.assertEqual(len(out["tool_calls"]), 1)
        self.assertEqual(out["tool_calls"][0]["name"], "query_forecast_detail")

    def test_suggested_messages_capped_and_length_filtered(self) -> None:
        provider = FakeProvider(response={
            "text": "Tonight looks fine at 112 covers.",
            "tool_calls": [],
            "suggested_messages": [
                "ok",
                "Too long: " + "x" * 100,
                "Show yesterday's breakdown",
                "What's next?",
                "And one more thing",
            ],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertLessEqual(len(result.outputs[0]["suggested_messages"]), 3)

    def test_staleness_notice_prepended(self) -> None:
        payload = _base_payload()
        payload["digest_staleness"] = {"current_state_age_seconds": 7200, "source_hash_match": True}
        provider = FakeProvider(response={
            "text": "Tonight looks steady at 112 covers.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertIn("snapshot", result.outputs[0]["text"].lower())

    def test_source_hash_mismatch_triggers_notice(self) -> None:
        payload = _base_payload()
        payload["digest_staleness"] = {"current_state_age_seconds": 10, "source_hash_match": False}
        provider = FakeProvider(response={
            "text": "Tonight looks steady at 112 covers.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertIn("snapshot", result.outputs[0]["text"].lower())

    def test_fallback_on_provider_none(self) -> None:
        provider = FakeProvider(response=None)
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertIn("112", result.outputs[0]["text"])
        self.assertEqual(len(result.outputs[0]["suggested_messages"]), 3)
        self.assertIn("deterministic fallback", result.rationale)

    def test_setup_fallback_still_adds_readiness_tool_call(self) -> None:
        payload = _base_payload()
        payload["operator_message"] = "Am I ready to forecast?"
        payload["current_state_digest"]["phase"] = "setup"
        payload["current_state_digest"]["headline_forecast"] = None
        provider = FakeProvider(response=None)
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertEqual(result.outputs[0]["tool_calls"][0]["name"], "check_readiness")
        self.assertEqual(result.outputs[0]["suggested_messages"], ["Add cover counts", "Check readiness", "Add patio info"])

    def test_fallback_on_provider_exception(self) -> None:
        provider = FakeProvider(raises=RuntimeError("boom"))
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertIn("deterministic fallback", result.rationale)
        self.assertIn("RuntimeError", result.rationale)

    def test_empty_text_triggers_fallback(self) -> None:
        provider = FakeProvider(response={"text": "", "tool_calls": [], "suggested_messages": []})
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(_base_payload()))
        self.assertEqual(result.status, AgentStatus.OK)
        self.assertIn("112", result.outputs[0]["text"])

    def test_tool_result_number_is_groundable(self) -> None:
        payload = _base_payload()
        payload["tool_results"] = [{"service_date": "2026-04-11", "actual_total": 82, "forecast_expected": 110}]
        provider = FakeProvider(response={
            "text": "Last Thursday came in at 82 covers against a forecast of 110.",
            "tool_calls": [],
            "suggested_messages": [],
        })
        agent = ConversationOrchestratorAgent(self.policy, provider)
        result = agent.run(_ctx(payload))
        text = result.outputs[0]["text"]
        self.assertIn("82", text)
        self.assertIn("110", text)


if __name__ == "__main__":
    unittest.main()
