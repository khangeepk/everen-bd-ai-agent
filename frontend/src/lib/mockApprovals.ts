import type { ApprovalDraft, SendQuota } from "@/types/approval";

/**
 * Mock approval-queue data for the review screen. This phase renders design +
 * interactions (keyboard, bulk confirm, provenance) against fixtures; wiring
 * to the real GET /outreach/queue + approve/reject endpoints is a later pass.
 */
export const mockSendQuota: SendQuota = {
  dailyLimit: 50,
  sentToday: 18,
};

export const mockApprovalDrafts: ApprovalDraft[] = [
  {
    id: "a1",
    leadName: "Riverside Auto Repair",
    industry: "Auto repair",
    location: "Austin, TX 78745",
    score: 0.84,
    scoreReasons: ["old website", "no mobile version", "contact email found"],
    channel: "email",
    subject: "Quick note about your website",
    body:
      "Hi — I noticed your site takes about 8 seconds to load and isn't built for phones, " +
      "which is where most of your customers are searching. We help auto repair shops fix " +
      "exactly that so more local searchers become booked jobs. Worth a quick chat?",
    problems: [
      { category: "performance", detail: "loads in ~8s" },
      { category: "mobile" },
      { category: "seo" },
    ],
    recommendedService: "Website redesign + local SEO",
    claims: [
      { phrase: "takes about 8 seconds to load", source: "performance", evidence: "8.2s load time" },
      { phrase: "isn't built for phones", source: "mobile", evidence: "no mobile viewport" },
      { phrase: "where most of your customers are searching", source: "seo" },
    ],
  },
  {
    id: "a2",
    leadName: "Bella Vita Trattoria",
    industry: "Restaurant",
    location: "Arlington, TX 76006",
    score: 0.66,
    scoreReasons: ["weak social presence", "no SSL"],
    channel: "email",
    subject: "Helping diners find you online",
    body:
      "Hello — your website doesn't have a secure padlock, so browsers may warn diners away, " +
      "and your social pages look inactive. We help restaurants look trustworthy online and " +
      "show up when people search for a place to eat nearby.",
    problems: [
      { category: "security" },
      { category: "social" },
    ],
    recommendedService: "Reputation + social presence setup",
    claims: [
      { phrase: "doesn't have a secure padlock", source: "security", evidence: "no HTTPS" },
      { phrase: "social pages look inactive", source: "social" },
    ],
  },
  {
    id: "a3",
    leadName: "Paris HVAC & Cooling",
    industry: "HVAC",
    location: "Paris, TX 75462",
    score: 0.9,
    scoreReasons: ["no website"],
    channel: "email",
    subject: "You're hard to find online",
    body:
      "Hi — I couldn't find a website for your business, which means customers searching for " +
      "HVAC help nearby are finding your competitors instead. We can get you online fast.",
    problems: [{ category: "seo" }, { category: "contact_form" }],
    recommendedService: "New website + Google Business Profile",
    claims: [
      { phrase: "couldn't find a website", source: "seo" },
      { phrase: "finding your competitors instead", source: "seo" },
    ],
    complianceState: "unsubscribed",
  },
];
