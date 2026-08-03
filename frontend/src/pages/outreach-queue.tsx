import { Inbox, Linkedin, RefreshCw } from "lucide-react";
import Head from "next/head";
import { useEffect, useState } from "react";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { AppShell } from "@/components/layout/AppShell";
import { LinkedInDraftCard } from "@/components/outreach/LinkedInDraftCard";
import { fetchLinkedInQueue, type LinkedInQueueOutcome } from "@/lib/outreachQueueApi";

/**
 * LinkedIn outreach queue: every pending-review LinkedIn draft (a
 * connection-request note + follow-up message, see backend/app/agents/
 * outreach.py::generate_linkedin_content), each with its own "Copy to
 * clipboard" action so a rep can paste the text into LinkedIn and send it
 * manually. This system has no LinkedIn integration -- see backend/app/
 * services/outreach_policy.py's module docstring -- so copy-to-clipboard is
 * the entire interaction this page supports; there is no send button here
 * and there should never be one.
 *
 * The first real (non-mock) drafts-review surface in this frontend: it
 * calls GET /outreach/queue (channel=linkedin, status=pending_review) and
 * GET /leads/{id} directly, falling back to clearly-labeled sample drafts
 * when no NEXT_PUBLIC_DEV_API_TOKEN is configured or a call fails -- same
 * real/mock split already established by the dashboard chat panel (see
 * src/lib/apiClient.ts, src/lib/outreachQueueApi.ts). A default export is
 * required here because this is a Next.js page file (the one sanctioned
 * exception to AGENTS.md section 4.1's named-exports rule).
 */
// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function OutreachQueuePage(): JSX.Element {
  const [outcome, setOutcome] = useState<LinkedInQueueOutcome | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  async function load(): Promise<void> {
    setIsLoading(true);
    const result = await fetchLinkedInQueue();
    setOutcome(result);
    setIsLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- LinkedIn queue</title>
      </Head>

      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Linkedin className="h-5 w-5 text-slate-600" aria-hidden />
          <h1 className="text-lg font-semibold text-slate-800">LinkedIn outreach queue</h1>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={isLoading}
          className="flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm ring-1 ring-slate-200 hover:bg-slate-100 disabled:opacity-50"
        >
          <RefreshCw className={isLoading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} aria-hidden />
          Refresh
        </button>
      </div>

      <p className="mb-4 max-w-2xl text-xs text-slate-500">
        Connection-request notes and follow-up messages, generated for leads who replied
        interested or asked to book a call. Nothing here is ever sent automatically -- copy
        each piece of text and send it yourself from your own LinkedIn account.
      </p>

      {outcome?.isMock ? (
        <div className="mb-4 rounded-md bg-amber-50 px-3 py-2 text-xs font-medium uppercase tracking-wide text-amber-700">
          Sample data -- not a real query. No local dev session could be obtained (is the
          backend running at NEXT_PUBLIC_API_BASE_URL?).
        </div>
      ) : null}

      {isLoading && !outcome ? (
        <p className="text-sm text-slate-400">Loading queue&hellip;</p>
      ) : outcome?.error ? (
        <ErrorState message={outcome.error} onRetry={() => void load()} />
      ) : outcome && outcome.items.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No drafts waiting yet"
          guidance="When the agent finds a good-fit lead, its LinkedIn draft shows up here for you to review and copy. Start by finding leads to fill this queue."
          cta={{ label: "Find leads", href: "/" }}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {outcome?.items.map((item) => <LinkedInDraftCard key={item.draftId} item={item} />)}
        </div>
      )}
    </AppShell>
  );
}
