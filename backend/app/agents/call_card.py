"""Call-center lead card generator.

Assembles the briefing a call-center rep sees the moment a lead enters
pipeline stage Hot: contact info, the problems the website audit found, the
recommended Everen Techno service, the full message history with the lead,
and a suggested call script.

Reuses `OutreachDraftAgent`'s findings/service selection and call-script
generation (app/agents/outreach.py) rather than duplicating that logic, so
the card's "what's wrong" and "what do we sell them" always match what the
outreach drafts for the same lead would say.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.outreach import AGENT_NAME as OUTREACH_AGENT_NAME
from app.agents.outreach import OutreachDraftAgent
from app.db.models.audit import AuditFinding
from app.db.models.knowledge_base import Service
from app.db.models.lead import Lead
from app.db.models.outreach import DraftStatus, OutreachDraft
from app.db.models.pipeline import CallCenterCard, InboundMessage
from app.services.knowledge_base import KnowledgeBaseService

logger = logging.getLogger(__name__)

AGENT_NAME = "call-center-card-agent-v1"

#: Findings beyond this count clutter the card; a rep needs the headline
#: problems, not a full audit dump.
_MAX_PROBLEMS_ON_CARD = 5


class CallCenterCardAgent:
    """Generates a `CallCenterCard` for a lead that has entered pipeline stage Hot."""

    def __init__(self, db: AsyncSession, kb: KnowledgeBaseService) -> None:
        """Initialize the agent.

        Args:
            db: Active database session.
            kb: Knowledge base service, passed through to the reused
                `OutreachDraftAgent` for service matching.
        """
        self._db = db
        self._outreach_agent = OutreachDraftAgent(db, kb)

    async def _message_history(self, lead_id: uuid.UUID) -> str:
        """Build a chronological markdown transcript of a lead's conversation.

        Merges logged inbound messages with sent outreach drafts (email and
        WhatsApp only -- an unsent draft is not part of the conversation that
        happened, and a call script is briefing material, not a transcript
        entry) into one time-ordered history.

        Args:
            lead_id: The lead to build history for.

        Returns:
            A markdown-formatted transcript, oldest first. A placeholder
            line if nothing has been exchanged yet.
        """
        inbound = (
            (
                await self._db.execute(
                    select(InboundMessage)
                    .where(InboundMessage.lead_id == lead_id)
                    .order_by(InboundMessage.received_at)
                )
            )
            .scalars()
            .all()
        )
        sent_drafts = (
            (
                await self._db.execute(
                    select(OutreachDraft)
                    .where(
                        OutreachDraft.lead_id == lead_id,
                        OutreachDraft.status == DraftStatus.SENT,
                        OutreachDraft.sent_at.is_not(None),
                    )
                    .order_by(OutreachDraft.sent_at)
                )
            )
            .scalars()
            .all()
        )

        entries: list[tuple[object, str]] = []
        for msg in inbound:
            intent_note = (
                f" _(classified: {msg.classified_intent.value})_"
                if msg.classified_intent is not None
                else ""
            )
            entries.append(
                (
                    msg.received_at,
                    f"**Lead ({msg.channel.value}, {msg.received_at.isoformat()}):**"
                    f"{intent_note}\n{msg.body}",
                )
            )
        for draft in sent_drafts:
            entries.append(
                (
                    draft.sent_at,
                    f"**Everen Techno ({draft.channel.value}, "
                    f"{draft.sent_at.isoformat() if draft.sent_at else 'unknown time'}):**\n"
                    f"{(draft.subject + chr(10)) if draft.subject else ''}{draft.body}",
                )
            )

        if not entries:
            return "(no prior messages on record)"

        entries.sort(key=lambda pair: pair[0])
        return "\n\n---\n\n".join(text for _, text in entries)

    @staticmethod
    def _problems_summary(findings: list[AuditFinding]) -> str:
        """Format audit findings as a rep-readable problems list.

        Args:
            findings: Selected audit findings, most severe first.

        Returns:
            A markdown bullet list, or a placeholder if none exist.
        """
        if not findings:
            return "(no website audit findings on record)"
        selected = findings[:_MAX_PROBLEMS_ON_CARD]
        return "\n".join(f"- **{f.title}** ({f.severity.value}): {f.detail}" for f in selected)

    @staticmethod
    def _service_summary(service: Service | None) -> str | None:
        """Format the recommended service for the card.

        Args:
            service: The matched service, if any.

        Returns:
            A one-line summary, or None if nothing matched.
        """
        if service is None:
            return None
        return f"{service.name} ({service.price_range_label()}): {service.summary}"

    async def generate(
        self, lead: Lead, *, triggering_message: InboundMessage | None = None
    ) -> CallCenterCard:
        """Generate and persist a call-center card for a Hot lead.

        Args:
            lead: The lead that just entered pipeline stage Hot.
            triggering_message: The inbound message whose classification put
                the lead into Hot, if this was reply-driven.

        Returns:
            The persisted (but not yet committed -- caller commits)
            `CallCenterCard`.
        """
        audit, findings = await self._outreach_agent.top_findings(lead.id)
        service = await self._outreach_agent.best_service(lead, findings)
        script_content = await self._outreach_agent.generate_call_script(
            lead, findings, service
        )
        history = await self._message_history(lead.id)

        card = CallCenterCard(
            lead_id=lead.id,
            triggering_message_id=triggering_message.id if triggering_message else None,
            contact_name=lead.contact_name,
            contact_title=lead.contact_title,
            contact_email=lead.contact_email,
            contact_phone=lead.contact_phone,
            problems_summary=self._problems_summary(findings),
            recommended_service_id=service.id if service else None,
            recommended_service_summary=self._service_summary(service),
            message_history_markdown=history,
            call_script=script_content.body,
            generated_by_agent=f"{AGENT_NAME}+{OUTREACH_AGENT_NAME}",
            used_fallback=script_content.used_fallback,
        )
        self._db.add(card)
        await self._db.flush()

        logger.info(
            "Call-center card generated",
            extra={
                "lead_id": str(lead.id),
                "card_id": str(card.id),
                "audit_id": str(audit.id) if audit else None,
                "service_id": str(service.id) if service else None,
                "used_fallback": card.used_fallback,
            },
        )
        return card
