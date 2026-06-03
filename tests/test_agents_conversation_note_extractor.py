from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from stormready_v3.agents.base import AgentContext, AgentRole, AgentStatus
from stormready_v3.agents.conversation_note_extractor import ConversationNoteExtractorAgent
from stormready_v3.agents.policy_loader import load_policy
from stormready_v3.domain.enums import ServiceState


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
        role=AgentRole.CONVERSATION_NOTE_EXTRACTOR,
        operator_id="op1",
        run_id="run_note",
        triggered_at=datetime.now(UTC),
        payload=payload,
    )


class ConversationNoteExtractorAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(AgentRole.CONVERSATION_NOTE_EXTRACTOR)

    def test_well_formed_response_is_normalized(self) -> None:
        provider = FakeProvider(
            response={
                "suggested_service_state": ServiceState.PRIVATE_EVENT.value,
                "suggested_correction": {
                    "realized_total_covers": "118",
                    "made_up": "999",
                    "realized_reserved_covers": "not a number",
                },
                "qualitative_themes": ["private_event", ""],
                "extracted_facts": {
                    "walk_in_state": "soft",
                    "unknown_fact": "drop",
                },
                "observations": [
                    {
                        "runtime_target": "walk_in_mix_review",
                        "direction": "negative",
                        "strength": "medium",
                        "summary": "Walk-ins were softer than expected.",
                    },
                    {"runtime_target": "made_up", "direction": "positive", "strength": "high"},
                ],
            }
        )
        agent = ConversationNoteExtractorAgent(self.policy, provider)

        result = agent.run(_ctx({"note": "Private event; total was 118 and walk-ins were soft."}))

        self.assertEqual(result.status, AgentStatus.OK)
        out = result.outputs[0]
        self.assertEqual(out["suggested_service_state"], ServiceState.PRIVATE_EVENT.value)
        self.assertEqual(out["suggested_correction"], {"realized_total_covers": "118"})
        self.assertEqual(out["qualitative_themes"], ["private_event"])
        self.assertEqual(out["extracted_facts"], {"walk_in_state": "soft"})
        self.assertEqual(len(out["observations"]), 1)
        self.assertEqual(out["observations"][0]["runtime_target"], "walk_in_mix_review")
        self.assertEqual(out["hypothesis_hints"], ["pattern::walk_in_mix"])

    def test_observations_are_synthesized_from_facts(self) -> None:
        provider = FakeProvider(
            response={
                "extracted_facts": {
                    "patio_demand_state": "strong",
                    "reservation_falloff": True,
                    "staffing_constraint": True,
                    "access_issue": True,
                }
            }
        )
        agent = ConversationNoteExtractorAgent(self.policy, provider)

        result = agent.run(_ctx({"note": "Patio was packed, but reservations fell off and staffing was tight."}))

        self.assertEqual(result.status, AgentStatus.OK)
        targets = [item["runtime_target"] for item in result.outputs[0]["observations"]]
        self.assertEqual(targets, ["weather_patio_profile", "reservation_anchor_review", "service_constraints"])
        self.assertEqual(
            result.outputs[0]["hypothesis_hints"],
            [
                "pattern::weather_patio_risk",
                "pattern::reservation_falloff",
                "pattern::staffing_constraint",
            ],
        )

    def test_blank_note_returns_empty_without_calling_provider(self) -> None:
        provider = FakeProvider(response={})
        agent = ConversationNoteExtractorAgent(self.policy, provider)

        result = agent.run(_ctx({"note": "   "}))

        self.assertEqual(result.status, AgentStatus.EMPTY)
        self.assertEqual(provider.calls, 0)

    def test_provider_none_returns_empty(self) -> None:
        provider = FakeProvider(response=None)
        agent = ConversationNoteExtractorAgent(self.policy, provider)

        result = agent.run(_ctx({"note": "Rain kept the patio closed."}))

        self.assertEqual(result.status, AgentStatus.EMPTY)

    def test_provider_exception_returns_failed(self) -> None:
        provider = FakeProvider(raises=RuntimeError("boom"))
        agent = ConversationNoteExtractorAgent(self.policy, provider)

        result = agent.run(_ctx({"note": "Rain kept the patio closed."}))

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertIn("RuntimeError", result.error or "")


if __name__ == "__main__":
    unittest.main()
