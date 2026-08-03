import { CheckSquare, Inbox } from "lucide-react";
import Head from "next/head";
import { useCallback, useEffect, useState } from "react";

import { ApprovalReviewScreen } from "@/components/approvals/ApprovalReviewScreen";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { AppShell } from "@/components/layout/AppShell";
import {
  approveDraft,
  fetchApprovalQueue,
  rejectDraft,
  type ApprovalQueueOutcome,
} from "@/lib/approvalsApi";

/**
 * Approval queue -- the reviewer's daily-driver screen. Keyboard-first,
 * one-screen review of each draft with its lead context, detected problems,
 * recommended service, claim provenance, live send-quota, and bulk approve.
 *
 * Backed by the real, joined GET /outreach/queue/enriched + GET /outreach/quota
 * (see lib/approvalsApi.ts) when a dev session is available; approve/reject/
 * bulk-approve call the real gated endpoints too -- this screen no longer
 * only pretends to approve something. Falls back to clearly-labeled sample
 * drafts when no session is available, and to an explicit error+retry (never
 * silent samples) when a real call fails. A default export is required here
 * (Next.js page file).
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function ApprovalsPage(): JSX.Element {
  const [outcome, setOutcome] = useState<ApprovalQueueOutcome | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const load = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    const result = await fetchApprovalQueue();
    setOutcome(result);
    setIsLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Re-fetch after a mutation so the reviewed draft drops out of the queue
   * and the quota bar reflects the change -- the queue is always a live view
   * of "what's still pending_review", not a client-side illusion of one. */
  const refresh = useCallback(() => {
    void load();
  }, [load]);

  const handleApprove = useCallback(
    (id: string) => {
      approveDraft(id)
        .then(refresh)
        .catch((error: unknown) => {
          // eslint-disable-next-line no-console -- surfaced to the console
          // for now; a toast/inline error for a single failed mutation
          // (as opposed to the whole queue failing to load) is a further
          // UX pass, not silently swallowed here.
          console.error("Failed to approve draft", error);
        });
    },
    [refresh],
  );

  const handleReject = useCallback(
    (id: string, reason: string) => {
      rejectDraft(id, reason)
        .then(refresh)
        .catch((error: unknown) => {
          // eslint-disable-next-line no-console
          console.error("Failed to reject draft", error);
        });
    },
    [refresh],
  );

  const handleBulkApprove = useCallback(
    (ids: string[]) => {
      Promise.allSettled(ids.map((id) => approveDraft(id))).then(refresh);
    },
    [refresh],
  );

  // Only wire real mutations when the queue itself is real -- approving
  // against mock data has nothing server-side to call.
  const isLive = outcome !== null && !outcome.isMock && !outcome.error;

  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Approval queue</title>
      </Head>
      <div className="mb-4 flex items-center gap-2">
        <CheckSquare className="h-5 w-5 text-slate-600" aria-hidden />
        <h1 className="text-lg font-semibold text-slate-800">Approval queue</h1>
      </div>

      {outcome?.isMock ? (
        <div className="mb-4 rounded-md bg-amber-50 px-3 py-2 text-xs font-medium uppercase tracking-wide text-amber-700">
          Sample data -- not a real query. No local dev session available (is the backend
          running at NEXT_PUBLIC_API_BASE_URL?).
        </div>
      ) : null}

      {isLoading && !outcome ? (
        <p className="text-sm text-slate-400">Loading approval queue&hellip;</p>
      ) : outcome?.error ? (
        <ErrorState message={outcome.error} onRetry={refresh} />
      ) : outcome && outcome.drafts.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="Nothing waiting for review"
          guidance="Drafts show up here once the agent has generated outreach for a lead. Start by finding leads and generating drafts."
          cta={{ label: "Find leads", href: "/" }}
        />
      ) : outcome ? (
        <ApprovalReviewScreen
          drafts={outcome.drafts}
          quota={outcome.quota}
          onApprove={isLive ? handleApprove : undefined}
          onReject={isLive ? handleReject : undefined}
          onBulkApprove={isLive ? handleBulkApprove : undefined}
        />
      ) : null}
    </AppShell>
  );
}
