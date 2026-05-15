"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LanguageToggle } from "@/components/LanguageToggle";
import { useLanguage } from "@/components/LanguageContext";
import { loadSession } from "@/lib/session";

export function NavBar() {
  const pathname = usePathname();
  const { language, t } = useLanguage();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [toast, setToast] = useState("");
  const isEnglish = language === "en";
  const dailyItems = [
    { href: "/plaza", label: t.nav.plaza },
    { href: "/chat", label: t.nav.chat },
    { href: "/probes", label: t.nav.probes },
    { href: "/counterfactuals", label: t.nav.counterfactuals },
  ];
  const experimentItems = [
    { href: "/memory", label: t.nav.memory },
    { href: "/time-machine", label: t.nav.timeMachine },
    { href: "/import", label: t.nav.import },
    { href: "/lab", label: t.nav.lab },
  ];
  const navGroups = [
    { label: t.nav.daily, items: dailyItems },
    { label: t.nav.experiments, items: experimentItems },
  ];

  useEffect(() => {
    if (!toast) {
      return;
    }

    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function copyEvaluationLink() {
    const agentId = loadSession()?.agent_id;
    if (!agentId) {
      setToast(t.nav.inviteFriendMissingAgent);
      return;
    }

    const evaluationLink = `${window.location.origin}/evaluations/${agentId}`;
    try {
      await navigator.clipboard.writeText(evaluationLink);
      setToast(t.nav.inviteFriendCopied);
      setMobileMenuOpen(false);
    } catch {
      setToast(t.nav.inviteFriendCopyFailed);
    }
  }

  if (pathname === "/site-login") {
    return (
      <nav className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            className="inline-flex items-center rounded-full px-2 py-1 text-sm font-semibold tracking-tight text-gray-950 transition hover:bg-gray-100"
            href="/"
          >
            Loop
          </Link>
          <LanguageToggle />
        </div>
      </nav>
    );
  }

  return (
    <nav className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-2 px-4 py-3 sm:px-5">
        <Link
          className="inline-flex shrink-0 items-center rounded-full px-2 py-1 text-sm font-semibold tracking-tight text-gray-950 transition hover:bg-gray-100"
          href="/"
          onClick={() => setMobileMenuOpen(false)}
        >
          Loop
        </Link>
        <div className="hidden min-w-0 flex-1 items-center justify-end gap-2 lg:flex">
          <div className="flex min-w-0 flex-1 items-center justify-center">
            <div
              className={`flex min-w-0 items-center rounded-[28px] border border-gray-200 bg-gray-50/90 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] ${
                isEnglish ? "gap-1.5 px-1.5 py-1.5" : "gap-2 px-2 py-1.5"
              }`}
            >
              {navGroups.map((group, index) => (
                <div
                  className={`flex min-w-0 items-center ${
                    isEnglish ? "gap-1" : "gap-1.5"
                  }`}
                  key={group.label}
                >
                  <div
                    className={`rounded-full bg-white font-semibold uppercase text-gray-400 shadow-sm ${
                      isEnglish
                        ? "px-2 py-1 text-[10px] tracking-[0.12em]"
                        : "px-2.5 py-1 text-[11px] tracking-[0.14em]"
                    }`}
                  >
                    {group.label}
                  </div>
                  <div
                    className={`flex min-w-0 items-center ${
                      isEnglish ? "gap-0.5" : "gap-1"
                    }`}
                  >
                    {group.items.map((item) => (
                      <NavLink
                        compact={isEnglish}
                        href={item.href}
                        isActive={isActivePath(pathname, item.href)}
                        key={item.href}
                        label={item.label}
                      />
                    ))}
                  </div>
                  {index < navGroups.length - 1 ? (
                    <div className={`${isEnglish ? "mx-0.5" : "mx-1"} h-6 w-px bg-gray-200`} />
                  ) : null}
                </div>
              ))}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 rounded-full border border-gray-200 bg-white/95 p-1 shadow-sm">
            <InviteFriendButton
              compact={isEnglish}
              label={t.nav.inviteFriendBlindTest}
              onClick={copyEvaluationLink}
            />
            <LanguageToggle className="shrink-0" />
          </div>
        </div>
        <div className="flex items-center gap-2 lg:hidden">
          <LanguageToggle className="shrink-0" />
          <button
            aria-controls="mobile-nav-menu"
            aria-expanded={mobileMenuOpen}
            aria-label={mobileMenuOpen ? t.nav.closeMenu : t.nav.openMenu}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-700 shadow-sm transition hover:bg-gray-100 hover:text-gray-950"
            onClick={() => setMobileMenuOpen((isOpen) => !isOpen)}
            type="button"
          >
            <span className="sr-only">{t.nav.menu}</span>
            <span className="flex flex-col gap-1">
              <span className="block h-0.5 w-4 rounded bg-current" />
              <span className="block h-0.5 w-4 rounded bg-current" />
              <span className="block h-0.5 w-4 rounded bg-current" />
            </span>
          </button>
        </div>
      </div>
      {mobileMenuOpen ? (
        <div
          className="border-t border-gray-200 bg-white px-4 py-4 shadow-sm lg:hidden"
          id="mobile-nav-menu"
        >
          <div className="mx-auto grid max-w-7xl gap-4">
            {navGroups.map((group) => (
              <div key={group.label}>
                <p className="px-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                  {group.label}
                </p>
                <div className="mt-2 grid gap-1">
                  {group.items.map((item) => (
                    <NavLink
                      href={item.href}
                      isActive={isActivePath(pathname, item.href)}
                      key={item.href}
                      label={item.label}
                      onClick={() => setMobileMenuOpen(false)}
                    />
                  ))}
                </div>
              </div>
            ))}
            <InviteFriendButton
              className="justify-center"
              label={t.nav.inviteFriendBlindTest}
              onClick={copyEvaluationLink}
            />
          </div>
        </div>
      ) : null}
      {toast ? (
        <div
          aria-live="polite"
          className="fixed bottom-6 left-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-center text-sm font-medium text-emerald-700 shadow-lg"
          role="status"
        >
          {toast}
        </div>
      ) : null}
    </nav>
  );
}

function InviteFriendButton({
  className = "",
  compact = false,
  label,
  onClick,
}: {
  className?: string;
  compact?: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className={`inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full border border-emerald-200 bg-emerald-50 font-semibold text-emerald-700 shadow-sm transition hover:border-emerald-300 hover:bg-emerald-100 hover:text-emerald-800 ${
        compact ? "px-3.5 text-[13px]" : "px-4 text-sm"
      } ${className}`}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}

function NavLink({
  compact = false,
  href,
  isActive,
  label,
  onClick,
}: {
  compact?: boolean;
  href: string;
  isActive: boolean;
  label: string;
  onClick?: () => void;
}) {
  return (
    <Link
      className={`whitespace-nowrap rounded-full font-medium transition ${
        compact ? "px-2.5 py-1.5 text-[13px]" : "px-3 py-2 text-sm"
      } ${
        isActive
          ? "bg-gray-950 text-white shadow-sm"
          : "text-gray-600 hover:bg-white hover:text-gray-950"
      }`}
      href={href}
      onClick={onClick}
    >
      {label}
    </Link>
  );
}

function isActivePath(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}
