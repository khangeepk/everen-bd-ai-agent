/**
 * Onboarding persistence + next-action derivation.
 *
 * First-run state lives in ``localStorage`` (client-only). All reads are
 * SSR-safe: they return sensible defaults when ``window`` is undefined so the
 * server render never touches browser storage.
 */

import type { NextAction, OnboardingSelection, PendingWorkCounts } from "@/types/onboarding";

const COMPLETED_KEY = "everen.onboarding.completed";
const SELECTION_KEY = "everen.onboarding.selection";

/**
 * Whether the user has finished (or skipped) onboarding.
 *
 * @returns True if onboarding should be hidden. Defaults to True on the server
 *   so the wizard never flashes during SSR; the client re-checks on mount.
 */
export function hasCompletedOnboarding(): boolean {
  if (typeof window === "undefined") {
    return true;
  }
  return window.localStorage.getItem(COMPLETED_KEY) === "true";
}

/** Persist that onboarding is done so it won't show again. */
export function markOnboardingComplete(selection: OnboardingSelection): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(COMPLETED_KEY, "true");
  window.localStorage.setItem(SELECTION_KEY, JSON.stringify(selection));
}

/** Clear onboarding state so the wizard can be replayed (used by "restart tour"). */
export function resetOnboarding(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(COMPLETED_KEY);
  window.localStorage.removeItem(SELECTION_KEY);
}

/**
 * Pick the single highest-value pending task for the dashboard banner.
 *
 * Priority order is deliberate: approvals unblock real sends and are the most
 * time-sensitive, then hot leads, then replies, then follow-ups. Only the top
 * non-zero item is returned so the user is never shown a wall of choices.
 *
 * @param counts Current pending-work counts.
 * @returns The one action to surface, or an "all caught up" state.
 */
export function deriveNextAction(counts: PendingWorkCounts): NextAction {
  if (counts.draftsAwaitingApproval > 0) {
    const n = counts.draftsAwaitingApproval;
    return {
      headline: `${n} draft${n === 1 ? "" : "s"} waiting for your approval`,
      detail: "Nothing sends until you approve it. Clear these first.",
      ctaLabel: "Review drafts",
      href: "/outreach-queue",
      allCaughtUp: false,
    };
  }
  if (counts.hotLeadsToReview > 0) {
    const n = counts.hotLeadsToReview;
    return {
      headline: `${n} hot lead${n === 1 ? "" : "s"} ready to act on`,
      detail: "High-scoring leads the agent surfaced. Turn them into outreach.",
      ctaLabel: "View hot leads",
      href: "/deals",
      allCaughtUp: false,
    };
  }
  if (counts.repliesToClassify > 0) {
    const n = counts.repliesToClassify;
    return {
      headline: `${n} repl${n === 1 ? "y" : "ies"} to review`,
      detail: "Prospects replied. Check what they said and respond.",
      ctaLabel: "Open replies",
      href: "/deals",
      allCaughtUp: false,
    };
  }
  if (counts.followUpsDue > 0) {
    const n = counts.followUpsDue;
    return {
      headline: `${n} follow-up${n === 1 ? "" : "s"} due`,
      detail: "These leads went quiet. A nudge is scheduled and waiting.",
      ctaLabel: "See follow-ups",
      href: "/",
      allCaughtUp: false,
    };
  }
  return {
    headline: "You're all caught up",
    detail: "No pending approvals, hot leads, or replies right now.",
    ctaLabel: "Find new leads",
    href: "/",
    allCaughtUp: true,
  };
}
