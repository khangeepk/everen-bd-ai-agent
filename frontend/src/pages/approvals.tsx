import { CheckSquare } from "lucide-react";
import Head from "next/head";

import { ApprovalReviewScreen } from "@/components/approvals/ApprovalReviewScreen";
import { AppShell } from "@/components/layout/AppShell";
import { mockApprovalDrafts, mockSendQuota } from "@/lib/mockApprovals";

/**
 * Approval queue -- the reviewer's daily-driver screen. Keyboard-first,
 * one-screen review of each draft with its lead context, detected problems,
 * recommended service, claim provenance, live send-quota, and bulk approve.
 *
 * Runs on mock data this phase (see lib/mockApprovals.ts); wiring to the real
 * GET /outreach/queue + gated approve/reject endpoints is a later pass. A
 * default export is required here (Next.js page file).
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function ApprovalsPage(): JSX.Element {
  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Approval queue</title>
      </Head>
      <div className="mb-4 flex items-center gap-2">
        <CheckSquare className="h-5 w-5 text-slate-600" aria-hidden />
        <h1 className="text-lg font-semibold text-slate-800">Approval queue</h1>
      </div>
      <ApprovalReviewScreen drafts={mockApprovalDrafts} quota={mockSendQuota} />
    </AppShell>
  );
}
