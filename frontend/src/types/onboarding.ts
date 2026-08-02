/** Shared types for the guided first-run onboarding experience. */

/** The four onboarding steps, in order. */
export type OnboardingStepId = "services" | "location" | "review" | "approve";

/** What the user picks/confirms while going through the wizard. */
export interface OnboardingSelection {
  /** Whether the user confirmed the service list the agent will pitch. */
  servicesConfirmed: boolean;
  /** First target postal code, e.g. "78745". */
  postalCode: string;
  /** First target industry, e.g. "auto repair shops". */
  industry: string;
}

/**
 * A single "what should I do now?" recommendation: the highest-value pending
 * task, phrased as a plain-language call to action with a destination.
 */
export interface NextAction {
  /** Plain-language headline, e.g. "12 drafts waiting for your approval". */
  headline: string;
  /** One-line explanation of why this matters right now. */
  detail: string;
  /** Label for the action button, e.g. "Review drafts". */
  ctaLabel: string;
  /** Route the button links to, e.g. "/outreach-queue". */
  href: string;
  /** True when there is nothing pending (a friendly "all caught up" state). */
  allCaughtUp: boolean;
}

/** Counts the dashboard uses to decide the single highest-value next action. */
export interface PendingWorkCounts {
  draftsAwaitingApproval: number;
  hotLeadsToReview: number;
  repliesToClassify: number;
  followUpsDue: number;
}
