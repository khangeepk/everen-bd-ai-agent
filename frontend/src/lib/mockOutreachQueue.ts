/**
 * Sample LinkedIn queue data, shown when no API token is configured or a
 * real call fails -- mirrors src/lib/mockChatResults.ts's role for the chat
 * panel. Always clearly labeled in the UI (see src/pages/outreach-queue.tsx)
 * so a sample draft is never mistaken for a real one.
 */

import type { LinkedInQueueItem } from "@/types/outreachQueue";

export const mockLinkedInQueue: LinkedInQueueItem[] = [
  {
    draftId: "mock-draft-1",
    leadId: "mock-lead-1",
    leadName: "Riverside Roofing Co.",
    contactName: "Dana Whitfield",
    linkedinUrl: "https://www.linkedin.com/in/dana-whitfield-example",
    status: "pending_review",
    connectionNote:
      "Hi Dana, I help businesses like Riverside Roofing with their websites and noticed something worth a quick chat. Would love to connect.",
    followUpMessage:
      "Hi Dana, thanks for connecting. I noticed your site takes a while to load on mobile, which can quietly cost you leads. That's exactly the kind of thing our Website Revamp work addresses ($5k-$15k). Open to a short call?",
    warnings: [
      "LinkedIn drafts are text only. Copy the connection-request note and follow-up message and send them manually from your own LinkedIn account -- this system has no LinkedIn integration and must not be made to send or scrape automatically.",
    ],
    usedFallback: false,
    createdAt: "2026-07-30T15:12:00Z",
  },
  {
    draftId: "mock-draft-2",
    leadId: "mock-lead-2",
    leadName: "Blue Coast Dental",
    contactName: "Marcus Ihenacho",
    linkedinUrl: "https://www.linkedin.com/in/marcus-ihenacho-example",
    status: "pending_review",
    connectionNote:
      "Hi Marcus, I work with businesses in your space on their websites and online presence. Would love to connect.",
    followUpMessage:
      "Hi Marcus, thanks for connecting -- always good to meet folks in your space. Happy to share what we found if it's useful -- open to a short call?",
    warnings: [
      "LinkedIn drafts are text only. Copy the connection-request note and follow-up message and send them manually from your own LinkedIn account -- this system has no LinkedIn integration and must not be made to send or scrape automatically.",
    ],
    usedFallback: true,
    createdAt: "2026-07-31T09:47:00Z",
  },
];
