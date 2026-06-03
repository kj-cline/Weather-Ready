from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from stormready_v3.conversation.service import ConversationContext, ConversationContextService
from stormready_v3.storage.db import Database
from stormready_v3.surfaces.operator_snapshot import OperatorRuntimeSnapshot, OperatorRuntimeSnapshotService


@dataclass(slots=True)
class OperatorStatePacket:
    operator_id: str
    reference_date: date
    snapshot: OperatorRuntimeSnapshot
    conversation: ConversationContext


def resolve_operator_current_time(
    *,
    timezone: str | None,
    current_time: datetime | None = None,
    active_date: date | None = None,
) -> datetime:
    tz = None
    if timezone:
        try:
            tz = ZoneInfo(timezone)
        except Exception:
            tz = None
    if current_time is None:
        resolved = datetime.now(tz or UTC)
    elif current_time.tzinfo is None:
        resolved = current_time.replace(tzinfo=tz or UTC)
    else:
        resolved = current_time.astimezone(tz or UTC)
    if active_date is not None:
        resolved = resolved.replace(
            year=active_date.year,
            month=active_date.month,
            day=active_date.day,
        )
    return resolved


class OperatorStatePacketService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.snapshot_service = OperatorRuntimeSnapshotService(db)
        self.conversation_service = ConversationContextService(db)

    def build_packet(
        self,
        *,
        operator_id: str,
        reference_date: date,
        current_time: datetime | None = None,
    ) -> OperatorStatePacket:
        snapshot = self.snapshot_service.build_snapshot(
            operator_id=operator_id,
            reference_date=reference_date,
        )
        conversation = self.conversation_service.build_context(
            operator_id=operator_id,
            reference_date=reference_date,
            snapshot=snapshot,
            current_time=current_time,
        )
        return OperatorStatePacket(
            operator_id=operator_id,
            reference_date=reference_date,
            snapshot=snapshot,
            conversation=conversation,
        )
