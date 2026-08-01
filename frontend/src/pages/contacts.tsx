import Head from "next/head";

import { AppShell } from "@/components/layout/AppShell";
import { ComingSoon } from "@/components/layout/ComingSoon";

// eslint-disable-next-line import/no-default-export -- Next.js requires a default export here.
export default function ContactsPage(): JSX.Element {
  return (
    <AppShell>
      <Head>
        <title>Everen BD Agent -- Contacts</title>
      </Head>
      <ComingSoon
        title="Contacts"
        description="A searchable contact directory (backed by the leads API's contact fields) lands in a later phase."
      />
    </AppShell>
  );
}
