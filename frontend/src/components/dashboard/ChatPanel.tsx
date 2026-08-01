import { Send, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ChatResultsTable } from "@/components/dashboard/ChatResultsTable";
import { PanelHeader } from "@/components/dashboard/PanelHeader";
import { hasApiToken } from "@/lib/apiClient";
import { LEADS_PAGE_SIZE, runLeadsQuery, runPlacesQuery, type ChatQueryOutcome } from "@/lib/chatQueries";
import { parseChatQuery } from "@/lib/parseChatQuery";
import type { ChatMessage, ParsedIntent } from "@/types/chat";

const EXAMPLE_PROMPTS: readonly string[] = [
  "find restaurants in Dallas with no website",
  "show me leads scored above 80",
  "show me leads with status qualified",
];

let messageCounter = 0;
function newMessageId(prefix: string): string {
  messageCounter += 1;
  return `${prefix}-${messageCounter}`;
}

/**
 * Dashboard chat panel: plain-text requests mapped to existing endpoints --
 * GET /leads and POST /places/search -- via src/lib/parseChatQuery.ts, with
 * results rendered in a shared table (ChatResultsTable). No new backend
 * logic anywhere in this feature; see src/lib/chatQueries.ts for exactly
 * which endpoints each intent calls.
 *
 * Falls back to realistic sample results when no
 * NEXT_PUBLIC_DEV_API_TOKEN is configured, or when a real call fails --
 * see hasApiToken()/ChatQueryOutcome in src/lib/apiClient.ts and
 * chatQueries.ts. Named export per AGENTS.md section 4.1.
 */
export function ChatPanel(): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isRunning, setIsRunning] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const text = inputValue.trim();
    if (!text || isRunning) {
      return;
    }

    setInputValue("");
    setMessages((previous) => [...previous, { id: newMessageId("user"), role: "user", text }]);
    setIsRunning(true);

    const intent = parseChatQuery(text);

    if (intent.kind === "unrecognized") {
      setMessages((previous) => [
        ...previous,
        { id: newMessageId("assistant"), role: "assistant", text: intent.message },
      ]);
      setIsRunning(false);
      return;
    }

    const outcome =
      intent.kind === "leads_list" ? await runLeadsQuery(intent) : await runPlacesQuery(intent);

    setMessages((previous) => [
      ...previous,
      {
        id: newMessageId("assistant"),
        role: "assistant",
        text: describeOutcome(intent, outcome),
        results: outcome.results,
        isMock: outcome.isMock,
      },
    ]);
    setIsRunning(false);
  }

  return (
    <section className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <PanelHeader
        icon={<Sparkles className="h-4 w-4 text-slate-500" aria-hidden />}
        title="Ask the pipeline"
      />

      {!hasApiToken() ? (
        <p className="mb-2 rounded-md bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
          No API token configured (NEXT_PUBLIC_DEV_API_TOKEN) -- every result below is sample
          data, not a real query.
        </p>
      ) : null}

      <div className="mb-3 max-h-[26rem] flex-1 space-y-3 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="text-xs text-slate-400">
            <p className="mb-2">Try one of these:</p>
            <ul className="space-y-1">
              {EXAMPLE_PROMPTS.map((prompt) => (
                <li key={prompt}>
                  <button
                    type="button"
                    onClick={() => setInputValue(prompt)}
                    className="rounded-md bg-slate-50 px-2 py-1 text-left text-slate-600 hover:bg-slate-100"
                  >
                    &quot;{prompt}&quot;
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {messages.map((message) => (
          <div
            key={message.id}
            className={
              message.role === "user"
                ? "ml-auto max-w-[85%] rounded-lg bg-brand-navy px-3 py-2 text-xs text-white"
                : "max-w-full rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700"
            }
          >
            <p>{message.text}</p>
            {message.isMock ? (
              <p className="mt-1 text-[10px] font-medium uppercase tracking-wide text-amber-600">
                Showing sample results
              </p>
            ) : null}
            {message.results ? <ChatResultsTable results={message.results} /> : null}
          </div>
        ))}

        {isRunning ? <p className="text-xs text-slate-400">Working on it&hellip;</p> : null}
      </div>

      <form onSubmit={handleSubmit} className="flex items-center gap-2">
        <input
          type="text"
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
          placeholder='e.g. "find restaurants in Dallas with no website"'
          className="flex-1 rounded-md border border-slate-200 px-3 py-2 text-xs focus:border-brand-blue focus:outline-none"
          disabled={isRunning}
        />
        <button
          type="submit"
          disabled={isRunning || !inputValue.trim()}
          aria-label="Send"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-navy text-white disabled:opacity-40"
        >
          <Send className="h-4 w-4" aria-hidden />
        </button>
      </form>
    </section>
  );
}

function describeOutcome(
  intent: Extract<ParsedIntent, { kind: "leads_list" | "places_search" }>,
  outcome: ChatQueryOutcome,
): string {
  const count = outcome.results.rows.length;
  const noun = intent.kind === "leads_list" ? "lead" : "business";
  const plural = count === 1 ? noun : `${noun}s`;
  let text = `Found ${count} ${plural}.`;

  if (intent.kind === "leads_list" && intent.minScorePercent !== undefined) {
    text += ` (Score checked across the most recent ${LEADS_PAGE_SIZE} leads matching your other filters -- not a full database scan.)`;
  }
  if (outcome.fallbackReason) {
    text += ` Couldn't reach the real API (${outcome.fallbackReason}), so these are sample results.`;
  }

  return text;
}
