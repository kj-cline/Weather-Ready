"""Build the AgentDispatcher at boot.

Called once from the FastAPI lifespan. Failing here should fail boot (same
pattern as config fail-fast) — a broken policy file must not be silently
tolerated.
"""

from __future__ import annotations

from ..ai.contracts import AgentModelProvider
from ..storage.db import Database
from .anomaly_explainer import AnomalyExplainerAgent
from .base import AgentDispatcher, AgentRole
from .conversation_note_extractor import ConversationNoteExtractorAgent
from .conversation_orchestrator import ConversationOrchestratorAgent
from .current_state_retriever import CurrentStateRetrieverAgent
from .logging_adapter import AgentRunLoggerAdapter
from .policy_loader import load_policy
from .prediction_governor import PredictionGovernorAgent
from .signal_interpreter import SignalInterpreterAgent
from .temporal_memory_retriever import TemporalMemoryRetrieverAgent


def build_agent_dispatcher(
    db: Database,
    provider: AgentModelProvider,
) -> AgentDispatcher:
    signal_policy = load_policy(AgentRole.SIGNAL_INTERPRETER)
    anomaly_policy = load_policy(AgentRole.ANOMALY_EXPLAINER)
    current_state_policy = load_policy(AgentRole.CURRENT_STATE_RETRIEVER)
    temporal_policy = load_policy(AgentRole.TEMPORAL_MEMORY_RETRIEVER)
    prediction_governor_policy = load_policy(AgentRole.PREDICTION_GOVERNOR)
    conversation_policy = load_policy(AgentRole.CONVERSATION_ORCHESTRATOR)
    conversation_note_policy = load_policy(AgentRole.CONVERSATION_NOTE_EXTRACTOR)
    agents = {
        AgentRole.SIGNAL_INTERPRETER: SignalInterpreterAgent(signal_policy, provider),
        AgentRole.ANOMALY_EXPLAINER: AnomalyExplainerAgent(anomaly_policy, provider),
        AgentRole.CURRENT_STATE_RETRIEVER: CurrentStateRetrieverAgent(
            current_state_policy, provider
        ),
        AgentRole.TEMPORAL_MEMORY_RETRIEVER: TemporalMemoryRetrieverAgent(
            temporal_policy, provider
        ),
        AgentRole.PREDICTION_GOVERNOR: PredictionGovernorAgent(
            prediction_governor_policy, provider
        ),
        AgentRole.CONVERSATION_ORCHESTRATOR: ConversationOrchestratorAgent(
            conversation_policy, provider
        ),
        AgentRole.CONVERSATION_NOTE_EXTRACTOR: ConversationNoteExtractorAgent(
            conversation_note_policy, provider
        ),
    }
    return AgentDispatcher(agents=agents, run_logger=AgentRunLoggerAdapter(db))
