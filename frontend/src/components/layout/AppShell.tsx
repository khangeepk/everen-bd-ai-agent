import type { ReactNode } from "react";

import { TopNav } from "@/components/layout/TopNav";

interface AppShellProps {
  children: ReactNode;
}

/**
 * Page chrome shared by every route: the top navigation bar plus a
 * max-width, padded content area. Named export per AGENTS.md section 4.1.
 */
export function AppShell({ children }: AppShellProps): JSX.Element {
  return (
    <div className="min-h-screen bg-[#f1f4f9]">
      <TopNav />
      <main className="mx-auto max-w-[1400px] px-6 py-6">{children}</main>
    </div>
  );
}
