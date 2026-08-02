/**
 * Plain-language translation layer.
 *
 * Turns internal jargon (score numbers, audit codes, stage/enum names,
 * compliance flags) into language a non-technical sales rep understands.
 * Everything the UI shows a rep should pass through here, so wording stays
 * consistent across the dashboard, lead detail, and approval queue.
 *
 * Enum values mirror the backend:
 *   score labels     -> app/services/lead_scoring.py (hot/warm/cold/do_not_contact)
 *   audit categories -> app/services/audit_scoring.py (FindingCategory)
 *   pipeline stages  -> app/services/pipeline.py (PipelineStage)
 *   suppression      -> app/db/models/outreach.py (SuppressionReason) + DNC / GDPR flags
 */

export type ScoreLabel = "hot" | "warm" | "cold" | "do_not_contact";
export type PipelineStage =
  | "new"
  | "contacted"
  | "interested"
  | "hot"
  | "meeting_booked"
  | "converted"
  | "lost";
export type AuditCategory =
  | "performance"
  | "seo"
  | "security"
  | "mobile"
  | "contact_form"
  | "social";
export type ComplianceState =
  | "unsubscribed"
  | "hard_bounce"
  | "spam_complaint"
  | "manual"
  | "do_not_contact"
  | "gdpr_deleted";

export interface ScorePresentation {
  label: string;
  tone: "hot" | "warm" | "cold" | "blocked";
}

/**
 * Map a 0–1 score to a rep-facing label (never show the bare number).
 * Thresholds match lead_scoring.py: HOT ≥ 0.75, WARM ≥ 0.50.
 */
export function scoreToLabel(score: number, doNotContact = false): ScorePresentation {
  if (doNotContact) {
    return { label: "Can't contact", tone: "blocked" };
  }
  if (score >= 0.75) {
    return { label: "Hot", tone: "hot" };
  }
  if (score >= 0.5) {
    return { label: "Warm", tone: "warm" };
  }
  return { label: "Cold", tone: "cold" };
}

/**
 * Build the one-line "why" that sits next to the label, e.g.
 * "Hot — old website, no mobile version, contact email found".
 *
 * @param reasons Short human reason fragments (already rep-friendly).
 */
export function scoreReasonLine(reasons: readonly string[]): string {
  if (reasons.length === 0) {
    return "No strong signals yet";
  }
  return reasons.slice(0, 3).join(", ");
}

/** Rewrite an audit finding as a business consequence, not a technical fact. */
export function auditConsequence(category: AuditCategory, detail?: string): string {
  const map: Record<AuditCategory, string> = {
    performance:
      "The site loads slowly — many visitors leave before it finishes opening.",
    mobile:
      "The site doesn't work well on phones — most customers browse on mobile and will bounce.",
    security:
      "The site isn't secure (no padlock) — browsers warn visitors away and Google ranks it lower.",
    seo: "The site is hard to find on Google — customers searching won't see this business.",
    contact_form:
      "There's no easy way to contact them on the site — leads have no simple way to reach out.",
    social:
      "Little or no social media presence — the business looks inactive to potential customers.",
  };
  const base = map[category];
  return detail ? `${base} (${detail})` : base;
}

/** What a pipeline stage means + what happens next — for tooltips. */
export function stageTooltip(stage: PipelineStage): { label: string; explains: string } {
  const map: Record<PipelineStage, { label: string; explains: string }> = {
    new: {
      label: "New",
      explains: "Just discovered. Next: the agent audits their site and drafts a message for you to approve.",
    },
    contacted: {
      label: "Contacted",
      explains: "Your outreach was sent. Next: wait for a reply — the agent watches for one and tells you.",
    },
    interested: {
      label: "Interested",
      explains: "They replied positively. Next: keep the conversation going toward a call.",
    },
    hot: {
      label: "Hot",
      explains: "Strong buying signals. Next: prioritise this lead — get them on a call soon.",
    },
    meeting_booked: {
      label: "Meeting booked",
      explains: "A call is on the calendar. Next: show up prepared — the agent built a lead card for you.",
    },
    converted: {
      label: "Won",
      explains: "They became a customer. Nothing more to do here — nice work.",
    },
    lost: {
      label: "Lost",
      explains: "This one didn't work out. It's closed and won't be contacted again.",
    },
  };
  return map[stage];
}

/** Plain sentence explaining why a lead can't be contacted (compliance state). */
export function complianceSentence(state: ComplianceState): string {
  const map: Record<ComplianceState, string> = {
    unsubscribed:
      "This person unsubscribed, so we can't email them again — it's the law (CAN-SPAM/GDPR).",
    hard_bounce:
      "Their email address doesn't exist or bounced, so sending would hurt your sender reputation.",
    spam_complaint:
      "They marked a previous email as spam, so we never contact them again.",
    manual:
      "Someone on your team marked this lead do-not-contact, so it's blocked on purpose.",
    do_not_contact:
      "This lead is flagged do-not-contact and is permanently blocked from any outreach.",
    gdpr_deleted:
      "This person asked us to delete their data (GDPR), so their details were erased and can't be used.",
  };
  return map[state];
}
