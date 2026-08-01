import { Bell, ChevronDown, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/router";

/** One item in the primary navigation bar. */
interface NavItem {
  href: string;
  label: string;
}

const NAV_ITEMS: readonly NavItem[] = [
  { href: "/", label: "Dashboard" },
  { href: "/deals", label: "deals" },
  { href: "/contacts", label: "contacts" },
  { href: "/partners", label: "partners" },
  { href: "/workflow", label: "workflow" },
  { href: "/outreach-queue", label: "LinkedIn queue" },
  { href: "/analytics", label: "analytics" },
];

/**
 * Primary application navigation bar: brand mark, page links, and the
 * mail/notification/account cluster on the right.
 *
 * Matches the layout of the reference dashboard design. Named export per
 * AGENTS.md section 4.1.
 */
export function TopNav(): JSX.Element {
  const router = useRouter();

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center justify-between bg-brand-navy px-6 text-white shadow-sm">
      <div className="flex items-center gap-10">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-white text-sm font-bold text-brand-navy">
            E
          </span>
          <span className="text-base font-semibold tracking-tight">Everen BD Agent</span>
        </div>

        <nav className="hidden items-center gap-7 text-sm text-blue-100 md:flex">
          {NAV_ITEMS.map((item) => {
            const isActive = router.pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={
                  isActive
                    ? "font-semibold text-white"
                    : "transition-colors hover:text-white"
                }
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="flex items-center gap-5">
        <Mail className="h-5 w-5 text-blue-100" aria-hidden />
        <div className="relative">
          <Bell className="h-5 w-5 text-blue-100" aria-hidden />
          <span className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-rose-500 text-[10px] font-semibold leading-none">
            1
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-200 text-xs font-semibold text-brand-navy"
            aria-hidden
          >
            SK
          </span>
          <ChevronDown className="h-4 w-4 text-blue-100" aria-hidden />
        </div>
      </div>
    </header>
  );
}
