import Head from "next/head";

import { AppShell } from "@/components/layout/AppShell";
import { ComingSoon } from "@/components/layout/ComingSoon";

// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function DealsPage(): JSX.Element {
  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Deals</title>
      </Head>
      <ComingSoon
        title="Deals"
        description="A full deal list/board view (backed by the leads + pipeline API) lands in a later phase."
      />
    </AppShell>
  );
}
