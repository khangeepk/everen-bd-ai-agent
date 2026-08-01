"""Public GDPR/CCPA privacy routes.

Mirrors ``/api/v1/outreach/unsubscribe``'s pattern deliberately: a data
subject completes their request with a single, unauthenticated page visit
using a tamper-proof link that was embedded in an email they actually
received (see ``build_erasure_url`` in ``app/services/canspam.py`` and its use
in ``app/agents/outreach.py::finalize_email_body``), rather than requiring an
account or a form. GDPR Article 17 (the right to erasure) does not mandate
this specific low-friction mechanic the way CAN-SPAM's opt-out rule does, but
there is no reason to hold data subjects to a higher bar for asking to be
forgotten than for asking to be unsubscribed.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import utcnow
from app.db.models.lead import Lead
from app.db.session import get_db
from app.schemas.privacy import DeleteRequestResponse
from app.services.canspam import verify_erasure_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/privacy", tags=["privacy"])

#: Fields scrubbed on erasure. `name` (the business name) and `category` are
#: deliberately NOT cleared -- they describe the business, not the person,
#: and this codebase's compliance model treats "may we hold PII about this
#: individual" and "may we know this business exists" as separate questions
#: (see Lead.do_not_contact vs Lead.status in app/db/models/lead.py).
_ERASED_TEXT_FIELDS: tuple[str, ...] = (
    "contact_name",
    "contact_title",
    "contact_phone",
    "website",
    "linkedin_url",
    "notes",
)


@router.get(
    "/delete-request",
    response_model=DeleteRequestResponse,
    summary="Fulfil a GDPR/CCPA data-erasure request",
    description=(
        "Public, unauthenticated -- reached from the erasure link embedded in every "
        "outreach email (see the footer built in app/services/canspam.py). Verifies "
        "a tamper-proof token bound to the specific lead and email address, then "
        "erases contact PII from that lead's record and flags it do-not-contact. "
        "The lead row itself is retained (not deleted) so foreign keys from audits, "
        "drafts, and pipeline events do not dangle -- pii_erased_at is the durable "
        "record that this happened."
    ),
)
async def delete_request(
    lead: uuid.UUID = Query(..., description="Lead identifier from the erasure link."),
    email: str = Query(..., description="Recipient address from the erasure link."),
    token: str = Query(..., description="HMAC verification token from the erasure link."),
    db: AsyncSession = Depends(get_db),
) -> DeleteRequestResponse:
    """Verify an erasure token and scrub the referenced lead's PII.

    Args:
        lead: The lead's identifier, from the link.
        email: The recipient's address, from the link.
        token: The HMAC token, from the link.
        db: Active database session.

    Returns:
        Confirmation the request was processed.

    Raises:
        HTTPException: 400 if the token does not verify.
    """
    if not verify_erasure_token(token, str(lead), email, settings.secret_key):
        logger.warning("Invalid erasure token presented", extra={"lead_id": str(lead)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid erasure request link."
        )

    row = await db.get(Lead, lead)
    if row is None or row.pii_erased_at is not None:
        # Idempotent and non-revealing: whether the lead never existed or was
        # already erased, the response looks the same either way so this
        # cannot be used to probe for which is true.
        logger.info(
            "Erasure request for missing or already-erased lead", extra={"lead_id": str(lead)}
        )
        return DeleteRequestResponse(
            message="No data found for this request, or it has already been processed.",
            erased=False,
        )

    for field_name in _ERASED_TEXT_FIELDS:
        setattr(row, field_name, None)
    row.set_contact_email(None)
    row.do_not_contact = True
    row.do_not_contact_reason = "GDPR/CCPA erasure request fulfilled"
    row.pii_erased_at = utcnow()
    await db.flush()

    logger.info("Lead PII erased on request", extra={"lead_id": str(lead)})
    return DeleteRequestResponse(
        message=(
            "Your data has been deleted and you will not be contacted again. "
            "No further action is needed."
        ),
        erased=True,
    )
