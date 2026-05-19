"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LanguageToggle } from "@/components/LanguageToggle";
import { useLanguage } from "@/components/LanguageContext";
import {
  clearAdminBackupSession,
  loadAdminBackupSession,
  loadSession,
  saveSession,
} from "@/lib/session";

export function NavBar() {
  const pathname = usePathname();
  const { t } = useLanguage();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [isImpersonating, setIsImpersonating] = useState(false);
  const isAdminView = isAdmin && !isImpersonating;
  const showUserUtility = !isAdminView;
  const navItems = isAdminView
    ? [
        { href: "/lab", label: t.nav.lab },
        { href: "/sandbox", label: t.nav.sandbox },
        { href: "/time-machine", label: t.nav.timeMachine },
        { href: "/import", label: t.nav.import },
      ]
    : [
        { href: "/plaza", label: t.nav.plaza },
        { href: "/chat", label: t.nav.chat },
        { href: "/social", label: t.nav.social },
        { href: "/memory", label: t.nav.memory },
        { href: "/probes", label: t.nav.probes },
        { href: "/counterfactuals", label: t.nav.counterfactuals },
      ];

  useEffect(() => {
    const session = loadSession();
    setIsAdmin(session?.is_admin === true);
    setIsImpersonating(Boolean(loadAdminBackupSession()));
  }, [pathname]);

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

  function exitImpersonation() {
    const adminSession = loadAdminBackupSession();
    if (!adminSession) {
      setIsImpersonating(false);
      return;
    }
    saveSession(adminSession);
    clearAdminBackupSession();
    setMobileMenuOpen(false);
    window.location.href = "/lab";
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
        <div className="hidden min-w-0 flex-1 items-center justify-end gap-3 lg:flex">
          <div className="flex min-w-0 flex-1 items-center justify-center gap-1.5">
            {navItems.map((item) => (
              <NavLink
                href={item.href}
                isActive={isActivePath(pathname, item.href)}
                key={item.href}
                label={item.label}
              />
            ))}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {isImpersonating ? (
              <ExitImpersonationButton
                label={t.nav.exitImpersonation}
                onClick={exitImpersonation}
              />
            ) : null}
            {showUserUtility ? (
              <InviteFriendButton
                label={t.nav.inviteFriendBlindTest}
                onClick={copyEvaluationLink}
              />
            ) : null}
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
          <div className="mx-auto grid max-w-7xl gap-1">
            {navItems.map((item) => (
              <NavLink
                href={item.href}
                isActive={isActivePath(pathname, item.href)}
                key={item.href}
                label={item.label}
                onClick={() => setMobileMenuOpen(false)}
              />
            ))}
            {isImpersonating ? (
              <ExitImpersonationButton
                className="mt-3 justify-center"
                label={t.nav.exitImpersonation}
                onClick={exitImpersonation}
              />
            ) : null}
            {showUserUtility ? (
              <InviteFriendButton
                className="justify-center"
                label={t.nav.inviteFriendBlindTest}
                onClick={copyEvaluationLink}
              />
            ) : null}
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

function ExitImpersonationButton({
  className = "",
  label,
  onClick,
}: {
  className?: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className={`inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full border border-rose-200 bg-rose-50 px-4 text-sm font-semibold text-rose-700 shadow-sm transition hover:border-rose-300 hover:bg-rose-100 hover:text-rose-800 ${className}`}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}

function InviteFriendButton({
  className = "",
  label,
  onClick,
}: {
  className?: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className={`inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full border border-emerald-200 bg-emerald-50 px-4 text-sm font-semibold text-emerald-700 shadow-sm transition hover:border-emerald-300 hover:bg-emerald-100 hover:text-emerald-800 ${className}`}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}

function NavLink({
  href,
  isActive,
  label,
  onClick,
}: {
  href: string;
  isActive: boolean;
  label: string;
  onClick?: () => void;
}) {
  return (
    <Link
      className={`whitespace-nowrap rounded-full px-3 py-2 text-sm font-medium transition ${
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
