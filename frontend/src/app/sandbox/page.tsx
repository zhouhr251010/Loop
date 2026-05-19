"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { DebatePanel } from "@/components/sandbox/DebatePanel";
import { GroupSimulationPanel } from "@/components/sandbox/GroupSimulationPanel";
import { apiRequest } from "@/lib/api";
import { useUiLanguage } from "@/lib/i18n";
import { LoopSession, loadSession } from "@/lib/session";
import { dictionary } from "@/locales/dictionary";

const DEFAULT_BRANCH_ID = "main";
const BRANCHES_ENDPOINT = "/api/simulation/branches";

export default function SandboxPage() {
  const router = useRouter();
  const { language } = useUiLanguage();
  const t = dictionary[language].sandbox;
  const [session, setSession] = useState<LoopSession | null>(null);
  const [branches, setBranches] = useState<string[]>([DEFAULT_BRANCH_ID]);
  const [branchId, setBranchId] = useState(DEFAULT_BRANCH_ID);
  const [error, setError] = useState("");
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);

  useEffect(() => {
    const storedSession = loadSession();
    if (!storedSession) {
      router.replace("/");
      return;
    }
    if (!storedSession.is_admin) {
      router.replace("/lab");
      return;
    }

    setSession(storedSession);
    setIsBootstrapping(false);
    void loadBranches();
  }, [router]);

  async function loadBranches(preferredBranch = branchId) {
    setIsLoadingBranches(true);
    setError("");
    try {
      const result = await apiRequest<unknown>(BRANCHES_ENDPOINT);
      const branchList = normalizeBranches(result);
      setBranches(branchList);
      if (branchList.includes(preferredBranch)) {
        setBranchId(preferredBranch);
      } else {
        setBranchId(DEFAULT_BRANCH_ID);
      }
    } catch (err) {
      setBranches([DEFAULT_BRANCH_ID]);
      setBranchId(DEFAULT_BRANCH_ID);
      setError(
        err instanceof Error
          ? t.branchUnavailable(err.message)
          : t.branchUnavailable(),
      );
    } finally {
      setIsLoadingBranches(false);
    }
  }

  if (isBootstrapping || !session) {
    return (
      <main className="mx-auto flex min-h-[60vh] max-w-6xl items-center justify-center px-4 py-10">
        <p className="rounded-2xl border border-gray-200 bg-white px-5 py-4 text-sm font-medium text-gray-600 shadow-sm">
          {t.loading}
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="grid gap-5 lg:grid-cols-[1fr_24rem] lg:items-end">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500">
              {t.eyebrow}
            </p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-gray-950 sm:text-3xl">
              {t.title}
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-600">
              {t.subtitle}
            </p>
          </div>
          <BranchSelector
            branches={branches}
            isLoading={isLoadingBranches}
            label={t.branchLabel}
            loadingLabel={t.refreshing}
            onChange={setBranchId}
            onRefresh={() => void loadBranches(branchId)}
            refreshLabel={t.refreshBranches}
            value={branchId}
          />
        </div>
        {error ? (
          <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-800">
            {error}
          </div>
        ) : null}
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <DebatePanel branchId={branchId} />
        <GroupSimulationPanel branchId={branchId} />
      </div>
    </main>
  );
}

function normalizeBranches(value: unknown) {
  const rawBranches = Array.isArray(value) ? value : [];
  const branches = rawBranches
    .map((item) => String(item ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set([DEFAULT_BRANCH_ID, ...branches]));
}
