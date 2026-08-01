# CAN-SPAM Compliance Checklist — Outreach Module

Reviewed against the FTC's seven CAN-SPAM requirements (15 U.S.C. § 7704).
Scope: `app/services/canspam.py`, `app/services/outreach_policy.py`,
`app/services/send_limits.py`, `app/services/suppression.py`, and
`app/api/v1/outreach.py`. This is a code-level compliance review, not legal
advice — confirm final wording and process with counsel before relying on it.

| # | Requirement | Status | Where enforced |
|---|---|---|---|
| 1 | No false or misleading header information (From/Reply-To must identify the actual sender) | **Pass** | `SenderIdentity.validate()` (`canspam.py`) requires a non-blank `from_name`, a `from_email` matching a valid address pattern, and rejects a malformed `reply_to`. Re-checked at send time by `validate_sendable_email()`. |
| 2 | No deceptive subject lines | **Pass** | `validate_subject()` / `is_deceptive_subject()` (`canspam.py`) rejects fake reply/forward prefixes (`Re:`, `Fwd:`), false urgency (`urgent`, `final notice`), and implied-existing-transaction framing (`your invoice/payment/order/account`). Heuristic, not exhaustive — the docstring is explicit that final judgment is a human reviewer's, which is consistent with the mandatory approval gate. |
| 3 | Identify the message as an advertisement | **Gap** | Not implemented. No draft — LLM-generated or fallback — includes explicit ad/commercial-message disclosure text. This is the one checklist item with no code enforcing it. See "Gaps" below. |
| 4 | Valid physical postal address | **Pass** | `SenderIdentity.validate()` requires `physical_address`, rejects blanks, values under 12 characters, and placeholder values (`REPLACE_ME`, `N/A`, `NONE`, `TBD`) — this is also enforced operationally: `outreach_physical_address` defaults to `REPLACE_ME` in `Settings`, so draft generation 422s until a real address is configured (`app/api/v1/outreach.py::generate_drafts`). The address is embedded in every footer (`build_footer()`) and its literal presence is re-verified in the assembled body at send time (`validate_sendable_email()`). |
| 5 | Clear, conspicuous opt-out mechanism, no more than a single step | **Pass** | Every email gets a one-click unsubscribe link (`build_unsubscribe_url()`), HMAC-signed so it can't be forged for a different draft/recipient (`make_unsubscribe_token()`/`verify_unsubscribe_token()`). The route (`GET /outreach/unsubscribe`) requires no login, no form, and no confirmation step — a single GET request completes the opt-out. `validate_sendable_email()` refuses to send if the link isn't literally present in the body. |
| 6 | Honor opt-outs within 10 business days, and the opt-out must not expire | **Pass, exceeds minimum** | Unsubscribe requests are processed synchronously and immediately (`unsubscribe()` in `outreach.py` calls `suppress()` and flags the lead `do_not_contact` in the same request) — not just within 10 business days. `SuppressionEntry.identifier` carries a permanent unique constraint with no expiry field at all, so a suppression can never lapse. Suppression is re-checked at both approval time (`approve_draft`) and immediately before dispatch (`send_draft`), not only at draft-creation time. |
| 7 | Monitor and remain responsible for what a third party sends on your behalf | **Partial** | SendGrid is the only third party that sends on this system's behalf. Delivery-failure and complaint events are ingested (`POST /outreach/webhooks/bounce`), classified (`classify_sendgrid_event()`), and hard bounces/spam complaints auto-suppress the address and flag the lead (`record_bounce()`, `should_suppress()`). **However**, the webhook endpoint's own docstring flags that it does not yet verify SendGrid's signed-event webhook signature, so today anyone who learns the URL could inject fabricated bounce/complaint events. This is a pre-existing, already-documented gap, not a new finding — see "Gaps" below. |

## Design choices that go beyond the checklist

- **Hard database constraints**, not just application logic, prevent a sent
  draft from lacking an approver
  (`ck_outreach_drafts_sent_requires_approver`, `ck_outreach_drafts_sent_at_matches_status`
  in `outreach_drafts`) — a bug in the send route can't silently produce a
  compliant-looking send that skipped human review.
- **`validate_sendable_email()` runs immediately before every dispatch**, not
  only at draft-approval time, so an edit that strips the footer after
  approval (`update_draft` is blocked for approved/sent drafts specifically to
  prevent this) or a configuration rollback (`OUTREACH_PHYSICAL_ADDRESS`
  reverted) is still caught at the last possible moment.
- **Daily send quota** (`send_limits.py`) is a deliverability/reputation
  control, not a CAN-SPAM requirement, but it bounds the blast radius of a
  draft-generation bug — relevant risk-management context even though it
  isn't one of the seven statutory items.
- **GDPR/CCPA erasure link** (added alongside this review — see
  `build_erasure_url()` in `canspam.py`) is not required by CAN-SPAM, which
  is US federal law only; it was added because recipients in GDPR/CCPA
  jurisdictions have a separate right to erasure, and there's no reason to
  hold them to a higher-friction bar for that than for the unsubscribe link
  next to it.

## Gaps (not fixed as part of this review — flagging for a decision)

1. **No "this is an advertisement" disclosure.** CAN-SPAM requires clear and
   conspicuous identification that a message is an advertisement, though the
   FTC does not mandate specific wording (a subject-line "AD:" prefix is one
   common approach, but so is disclosure elsewhere in the body). Fixing this
   would mean either appending a line to `build_footer()` or asking the draft
   agent's system prompts (`_EMAIL_SYSTEM_PROMPT` in `app/agents/outreach.py`)
   to disclose it in the body — a product/legal-tone decision (where it reads
   least like a hard sell) rather than a pure engineering one, so it wasn't
   made unilaterally here.
2. **SendGrid webhook signature verification is still a TODO**, exactly as
   the code's own docstring already says. Until it's added, the bounce
   webhook is an unauthenticated endpoint that can suppress arbitrary
   addresses if its URL leaks. This predates this review; flagging it here
   because it bears directly on requirement #7.
3. **Plain-text email only** (noted previously, in the Phase 7 analytics
   work) means there is no way to render an HTML "ADV" badge or styled
   disclosure even if requirement #3 above is addressed via the body — a
   text-only disclosure line is the only option until HTML email is adopted.

## Bottom line

5 of 7 requirements are fully enforced in code, one (#7, third-party
monitoring) is enforced except for webhook signature verification (a known,
already-documented gap), and one (#3, ad disclosure) has no code-level
enforcement at all. Nothing found here changes the "outreach can only be sent
by an approved, human-reviewed draft" guarantee (AGENTS.md section 8) — the
gaps are additive requirements on top of that gate, not weaknesses in it.
