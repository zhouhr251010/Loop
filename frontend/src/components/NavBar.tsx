"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LanguageToggle } from "@/components/LanguageToggle";
import { useLanguage } from "@/components/LanguageContext";

export function NavBar() {
  const pathname = usePathname();
  const { t } = useLanguage();
  const navItems = [
    { href: "/plaza", label: t.nav.plaza },
    { href: "/chat", label: t.nav.chat },
    { href: "/import", label: t.nav.import },
    { href: "/memory", label: t.nav.memory },
    { href: "/time-machine", label: t.nav.timeMachine },
    { href: "/lab", label: t.nav.lab },
  ];

  if (pathname === "/site-login") {
    return (
      <nav className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link className="text-sm font-bold tracking-tight text-gray-950" href="/">
            Loop
          </Link>
          <LanguageToggle />
        </div>
      </nav>
    );
  }

  return (
    <nav className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <Link className="text-sm font-bold tracking-tight text-gray-950" href="/">
          Loop
        </Link>
        <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                className={`shrink-0 rounded-full px-3.5 py-2 text-sm font-medium transition ${
                  isActive
                    ? "bg-gray-950 text-white shadow-sm"
                    : "text-gray-600 hover:bg-gray-100 hover:text-gray-950"
                }`}
                href={item.href}
                key={item.href}
              >
                {item.label}
              </Link>
            );
          })}
          <LanguageToggle className="shrink-0" />
        </div>
      </div>
    </nav>
  );
}
