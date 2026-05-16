"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BranchSelector } from "@/components/BranchSelector";
import { useLanguage } from "@/components/LanguageContext";
import {
  Agent,
  AgentSessionChoice,
  apiRequest,
  formatAgentChoiceLabel,
  formatAgentName,
} from "@/lib/api";
import type { Dictionary } from "@/locales/dictionary";
import { LoopSession, loadSession, saveSession } from "@/lib/session";
import { formatLocalDateTime, parseUtcTimestamp } from "@/lib/time";

const EVENT_PAGE_SIZE = 50;

const EVENT_HISTORY_PAGE_ENDPOINT = (
  agentId: number,
  branchId = "main",
  skip = 0,
  limit = EVENT_PAGE_SIZE,
) =>
  `/api/agents/${agentId}/events?branch_id=${encodeURIComponent(branchId)}&skip=${skip}&limit=${limit}`;

const BRANCHES_ENDPOINT = (agentId: number) =>
  `/api/simulation/agents/${agentId}/branches`;

type EventLog = {
  event_id: number;
  timestamp: string;
  agent_id: number;
  branch_id: string;
  event_type: string;
  payload: Record<string, unknown>;
};

type AgentState = {
  agent_id: number;
  branch_id: string;
  target_timestamp: string;
  core_memory: Record<string, unknown>;
  working_memory: Record<string, unknown>;
  intimacy: Record<string, number>;
  replayed_events: number;
};

type SimulationForkResponse = {
  branch_id: string;
  rollback_timestamp: string;
  injected_event: EventLog;
  reconstructed_state: AgentState;
};

type ForkDraft = {
  event: EventLog;
  newBranchName: string;
  counterfactualEvent: string;
};

export function TimeMachinePanel() {
  const router = useRouter();
  const { t } = useLanguage();
  const copy = t.timeMachine;
  const [session, setSession] = useState<LoopSession | null>(null);
  const [agentChoices, setAgentChoices] = useState<AgentSessionChoice[]>([]);
  const [targetAgentId, setTargetAgentId] = useState<number | null>(null);
  const [targetAgentLabel, setTargetAgentLabel] = useState("");
  const [branches, setBranches] = useState<string[]>(["main"]);
  const [selectedBranch, setSelectedBranch] = useState("main");
  const [events, setEvents] = useState<EventLog[]>([]);
  const [eventSkip, setEventSkip] = useState(0);
  const [hasMoreEvents, setHasMoreEvents] = useState(false);
  const [expandedDateGroups, setExpandedDateGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const [forkDraft, setForkDraft] = useState<ForkDraft | null>(null);
  const [forkResult, setForkResult] = useState<SimulationForkResponse | null>(
    null,
  );
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);
  const [isLoadingMoreEvents, setIsLoadingMoreEvents] = useState(false);
  const [isForking, setIsForking] = useState(false);

  const sortedEvents = useMemo(
    () =>
      [...events].sort((left, right) => {
        const byTime =
          parseUtcTimestamp(right.timestamp).getTime() -
          parseUtcTimestamp(left.timestamp).getTime();
        return byTime === 0 ? right.event_id - left.event_id : byTime;
      }),
    [events],
  );

  const eventGroups = useMemo(() => groupEventsByDay(sortedEvents), [sortedEvents]);

  useEffect(() => {
    async function bootstrap() {
      const storedSession = loadSession();
      if (!storedSession) {
        router.replace("/");
        return;
      }
      if (!storedSession.is_admin) {
        router.replace("/plaza");
        return;
      }

      setSession(storedSession);
      setTargetAgentId(storedSession.agent_id ?? null);
      setTargetAgentLabel(
        storedSession.agent_is_npc && storedSession.agent_name
          ? `${storedSession.agent_name} [NPC]`
          : storedSession.agent_name ?? storedSession.username,
      );

      if (storedSession.agent_id) {
        setIsBootstrapping(false);
        return;
      }

      try {
        const agent = await apiRequest<Agent>("/api/users/me/agent");
        const hydratedSession = {
          ...storedSession,
          agent_id: agent.id,
          agent_name: agent.agent_name,
          agent_is_npc: agent.is_npc,
        };
        saveSession(hydratedSession);
        setSession(hydratedSession);
        setTargetAgentId(agent.id);
        setTargetAgentLabel(agent.agent_name);
      } catch {
        setError(copy.noAgent);
      } finally {
        setIsBootstrapping(false);
      }
    }

    bootstrap();
  }, [router]);

  useEffect(() => {
    if (!targetAgentId) {
      setBranches(["main"]);
      setSelectedBranch("main");
      return;
    }

    loadBranches(targetAgentId);
  }, [targetAgentId]);

  async function loadAgentChoices() {
    if (!session?.is_admin) {
      setError(copy.adminOnly);
      return;
    }

    setError("");
    setMessage("");
    setIsLoadingAgents(true);
    try {
      const choices = await apiRequest<AgentSessionChoice[]>(
        "/api/users/agent-choices",
      );
      setAgentChoices(choices);
      setMessage(copy.loadedTargets(choices.length));
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.loadAgentsFailed);
    } finally {
      setIsLoadingAgents(false);
    }
  }

  async function loadBranches(agentId: number) {
    setIsLoadingBranches(true);
    try {
      const branchList = normalizeBranches(
        await apiRequest<unknown>(BRANCHES_ENDPOINT(agentId)),
      );
      setBranches(branchList);
      if (!branchList.includes(selectedBranch)) {
        setSelectedBranch("main");
      }
    } catch (err) {
      setBranches(["main"]);
      setSelectedBranch("main");
      setMessage("");
      setError(
        err instanceof Error ? err.message : copy.loadBranchesFailed,
      );
    } finally {
      setIsLoadingBranches(false);
    }
  }

  async function loadTimeline() {
    if (!targetAgentId) {
      setError(copy.chooseAgentFirst);
      return;
    }

    setError("");
    setMessage("");
    setForkResult(null);
    setIsLoadingEvents(true);
    try {
      const history = await apiRequest<EventLog[]>(
        EVENT_HISTORY_PAGE_ENDPOINT(targetAgentId, selectedBranch),
      );
      setEvents(history);
      setEventSkip(history.length);
      setHasMoreEvents(history.length === EVENT_PAGE_SIZE);
      setExpandedDateGroups(
        latestEventDateKey(history),
      );
      setMessage(copy.loadedEvents(history.length, selectedBranch));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : copy.loadTimelineFailed,
      );
    } finally {
      setIsLoadingEvents(false);
    }
  }

  async function loadMoreTimelineEvents() {
    if (
      !targetAgentId ||
      isLoadingEvents ||
      isLoadingMoreEvents ||
      !hasMoreEvents
    ) {
      return;
    }

    setError("");
    setIsLoadingMoreEvents(true);
    try {
      const history = await apiRequest<EventLog[]>(
        EVENT_HISTORY_PAGE_ENDPOINT(targetAgentId, selectedBranch, eventSkip),
      );
      setEvents((currentEvents) => {
        const existingIds = new Set(
          currentEvents.map((timelineEvent) => timelineEvent.event_id),
        );
        const uniqueOlderEvents = history.filter(
          (timelineEvent) => !existingIds.has(timelineEvent.event_id),
        );
        return [...currentEvents, ...uniqueOlderEvents];
      });
      setEventSkip((currentSkip) => currentSkip + history.length);
      setHasMoreEvents(history.length === EVENT_PAGE_SIZE);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : copy.loadTimelineFailed,
      );
    } finally {
      setIsLoadingMoreEvents(false);
    }
  }

  function chooseAgent(agentId: string) {
    const numericAgentId = Number(agentId);
    if (!Number.isFinite(numericAgentId)) {
      setTargetAgentId(null);
      setTargetAgentLabel("");
      setBranches(["main"]);
      setSelectedBranch("main");
      setEvents([]);
      setEventSkip(0);
      setHasMoreEvents(false);
      setExpandedDateGroups(new Set());
      return;
    }

    const choice = agentChoices.find((item) => item.agent.id === numericAgentId);
    setTargetAgentId(numericAgentId);
    setTargetAgentLabel(
      choice
        ? `${formatAgentName(choice.agent)} / @${choice.user.username}`
        : `Agent #${numericAgentId}`,
    );
    setSelectedBranch("main");
    setEvents([]);
    setEventSkip(0);
    setHasMoreEvents(false);
    setExpandedDateGroups(new Set());
    setForkResult(null);
  }

  function chooseBranch(branchId: string) {
    setSelectedBranch(branchId || "main");
    setEvents([]);
    setEventSkip(0);
    setHasMoreEvents(false);
    setExpandedDateGroups(new Set());
    setForkResult(null);
    setMessage("");
    setError("");
  }

  function toggleDateGroup(dateKey: string) {
    setExpandedDateGroups((currentGroups) => {
      const nextGroups = new Set(currentGroups);
      if (nextGroups.has(dateKey)) {
        nextGroups.delete(dateKey);
      } else {
        nextGroups.add(dateKey);
      }
      return nextGroups;
    });
  }

  function openForkModal(event: EventLog) {
    const timestamp = formatBranchTimestamp(event.timestamp);
    setForkDraft({
      event,
      newBranchName: `universe_${event.agent_id}_${event.event_id}_${timestamp}`,
      counterfactualEvent: "",
    });
    setError("");
    setMessage("");
    setForkResult(null);
  }

  function closeForkModal() {
    if (isForking) {
      return;
    }
    setForkDraft(null);
  }

  async function submitFork(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!forkDraft || !targetAgentId) {
      return;
    }
    if (!forkDraft.newBranchName.trim()) {
      setError(copy.branchNameRequired);
      return;
    }
    if (!forkDraft.counterfactualEvent.trim()) {
      setError(copy.counterfactualRequired);
      return;
    }

    setError("");
    setMessage("");
    setIsForking(true);
    try {
      const result = await apiRequest<SimulationForkResponse>(
        "/api/simulation/fork",
        {
          method: "POST",
          body: JSON.stringify({
            agent_id: targetAgentId,
            source_branch_id: selectedBranch,
            source_event_id: forkDraft.event.event_id,
            rollback_timestamp: forkDraft.event.timestamp,
            new_branch_name: forkDraft.newBranchName.trim(),
            counterfactual_event: parseCounterfactualEvent(
              forkDraft.counterfactualEvent,
            ),
          }),
        },
      );
      setForkResult(result);
      setBranches((currentBranches) =>
        normalizeBranches([...currentBranches, result.branch_id]),
      );
      setSelectedBranch(result.branch_id);
      setEvents([]);
      setEventSkip(0);
      setHasMoreEvents(false);
      setExpandedDateGroups(new Set());
      setMessage(copy.createdUniverse(result.branch_id));
      setForkDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : copy.forkFailed);
    } finally {
      setIsForking(false);
    }
  }

  if (isBootstrapping || !session) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6">
        <p className="text-sm text-gray-500">{copy.loading}</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-gray-50">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
        <header className="mb-6 border-b border-gray-200 pb-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">
            {t.nav.timeMachine}
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950">
            {copy.title}
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-500">
            {copy.subtitle}
          </p>
          <p className="mt-2 text-sm text-gray-400">
            {t.common.signedInAs}{" "}
            <span className="font-medium text-gray-600">@{session.username}</span>
            {" · "}
            <span className="font-medium text-gray-600">
              {session.agent_name ?? t.common.noAgentYet}
            </span>
          </p>
        </header>

        {message ? (
          <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 shadow-sm">
            {message}
          </div>
        ) : null}
        {error ? (
          <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-sm">
            {error}
          </div>
        ) : null}

        <section className="mb-5 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(180px,240px)_auto_auto]">
            {session.is_admin ? (
              <label className="block">
                <span className="text-sm font-medium text-gray-700">
                  {copy.timelineTarget}
                </span>
                <select
                  className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition focus:border-cyan-500 focus:bg-white focus:ring-4 focus:ring-cyan-100"
                  onChange={(event) => chooseAgent(event.target.value)}
                  value={targetAgentId ?? ""}
                >
                  {targetAgentId ? (
                    <option value={targetAgentId}>{targetAgentLabel}</option>
                  ) : (
                    <option value="">{t.common.chooseAgent}</option>
                  )}
                  {agentChoices.map((choice) => (
                    <option key={choice.agent.id} value={choice.agent.id}>
                      {formatAgentChoiceLabel(choice)}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  {copy.timelineTarget}
                </p>
                <p className="mt-1 truncate text-sm font-medium text-gray-800">
                  {targetAgentLabel || t.common.noAgentYet}
                </p>
              </div>
            )}
            <BranchSelector
              branches={branches}
              disabled={!targetAgentId}
              isLoading={isLoadingBranches}
              label={t.common.branchSelector}
              loadingLabel={t.common.loading}
              onChange={chooseBranch}
              onRefresh={() => targetAgentId && loadBranches(targetAgentId)}
              refreshLabel={t.common.refreshBranches}
              value={selectedBranch}
            />
            {session.is_admin ? (
              <div className="flex items-end">
                <button
                  className="w-full rounded-full border border-gray-200 bg-white px-4 py-3 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60 lg:w-auto"
                  disabled={isLoadingAgents}
                  onClick={loadAgentChoices}
                  type="button"
                >
                  {isLoadingAgents ? t.common.loading : copy.loadAgents}
                </button>
              </div>
            ) : null}
            <div className="flex items-end">
              <button
                className="w-full rounded-full bg-gray-950 px-4 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60 lg:w-auto"
                disabled={!targetAgentId || isLoadingEvents || isLoadingBranches}
                onClick={loadTimeline}
                type="button"
              >
                {isLoadingEvents ? t.common.loading : copy.loadTimeline}
              </button>
            </div>
          </div>
        </section>

        <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2
                  className={`text-lg font-semibold ${
                    selectedBranch === "main"
                      ? "text-indigo-950"
                      : "text-fuchsia-950"
                  }`}
                >
                  {copy.selectedBranchLog(selectedBranch)}
                </h2>
                <p className="mt-1 text-sm leading-6 text-gray-500">
                  {copy.currentAgent(targetAgentLabel)}
                </p>
              </div>
              <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-semibold text-gray-600">
                {copy.events(sortedEvents.length)}
              </span>
            </div>

            <div className="mt-6">
              {isLoadingEvents ? (
                <TimelineSkeleton />
              ) : eventGroups.length > 0 ? (
                <div className="space-y-3">
                  {eventGroups.map((group, groupIndex) => {
                    const isExpanded = expandedDateGroups.has(group.key);
                    return (
                      <section
                        className="rounded-xl border border-gray-200 bg-gray-50"
                        key={group.key}
                      >
                        <button
                          className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
                          onClick={() => toggleDateGroup(group.key)}
                          type="button"
                        >
                          <span className="min-w-0">
                            <span className="block text-sm font-semibold text-gray-950">
                              {group.label}
                            </span>
                            <span className="mt-0.5 block text-xs text-gray-500">
                              {copy.events(group.events.length)}
                            </span>
                          </span>
                          <span className="shrink-0 text-sm font-semibold text-gray-500">
                            {isExpanded ? copy.collapseGroup : copy.expandGroup}
                          </span>
                        </button>
                        {isExpanded ? (
                          <ol className="relative mx-4 border-l border-gray-200 pb-4 pl-6">
                            {group.events.map((timelineEvent, index) => (
                              <TimelineEventItem
                                event={timelineEvent}
                                isLast={
                                  groupIndex === eventGroups.length - 1 &&
                                  index === group.events.length - 1
                                }
                                key={timelineEvent.event_id}
                                onFork={openForkModal}
                              />
                            ))}
                          </ol>
                        ) : null}
                      </section>
                    );
                  })}
                  {hasMoreEvents ? (
                    <div className="flex justify-center pt-2">
                      <button
                        className="rounded-full border border-gray-200 bg-white px-5 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                        disabled={isLoadingMoreEvents}
                        onClick={loadMoreTimelineEvents}
                        type="button"
                      >
                        {isLoadingMoreEvents ? copy.loadingMore : copy.loadMoreEvents}
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : (
                <EmptyTimeline />
              )}
            </div>
          </div>

          <aside className="space-y-5">
            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="text-base font-semibold text-gray-950">
                {copy.branchStatus}
              </h2>
              {forkResult ? (
                <dl className="mt-4 space-y-3">
                  <Metric label={copy.metricNewBranch} value={forkResult.branch_id} />
                  <Metric
                    label={copy.metricRollback}
                    value={formatLocalDateTime(forkResult.rollback_timestamp)}
                  />
                  <Metric
                    label={copy.metricReplayedEvents}
                    value={forkResult.reconstructed_state.replayed_events}
                  />
                </dl>
              ) : (
                <p className="mt-3 text-sm leading-6 text-gray-500">
                  {copy.branchStatusEmpty}
                </p>
              )}
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="text-base font-semibold text-gray-950">
                {copy.payloadFormat}
              </h2>
              <p className="mt-3 text-sm leading-6 text-gray-500">
                {copy.payloadHelp}
              </p>
              <pre className="mt-3 overflow-auto rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-600">
{`{
  "event_type": "COUNTERFACTUAL_EVENT",
  "description": "..."
}`}
              </pre>
            </section>
          </aside>
        </section>
      </div>

      {forkDraft ? (
        <ForkModal
          draft={forkDraft}
          isSubmitting={isForking}
          onChange={setForkDraft}
          onClose={closeForkModal}
          onSubmit={submitFork}
        />
      ) : null}
    </main>
  );
}

function TimelineEventItem({
  event,
  isLast,
  onFork,
}: {
  event: EventLog;
  isLast: boolean;
  onFork: (event: EventLog) => void;
}) {
  const { t } = useLanguage();
  const summary = summarizeEvent(event, t.timeMachine);

  return (
    <li className={`relative ${isLast ? "pb-1" : "pb-7"}`}>
      <span className="absolute -left-[31px] top-1 flex h-4 w-4 items-center justify-center rounded-full border-2 border-white bg-cyan-600 shadow-sm" />
      <article className="rounded-xl border border-gray-200 bg-gray-50 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-gray-700 shadow-sm ring-1 ring-gray-200">
                #{event.event_id}
              </span>
              <span className="rounded-full bg-cyan-50 px-2.5 py-1 text-xs font-semibold text-cyan-700 ring-1 ring-cyan-100">
                {event.event_type}
              </span>
              <span className="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-gray-500 ring-1 ring-gray-200">
                {event.branch_id}
              </span>
            </div>
            <time
              className="mt-3 block text-sm font-medium text-gray-950"
              dateTime={parseUtcTimestamp(event.timestamp).toISOString()}
              title={parseUtcTimestamp(event.timestamp).toISOString()}
            >
              {formatLocalDateTime(event.timestamp)}
            </time>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-600">
              {summary}
            </p>
          </div>
          <button
            className="shrink-0 rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800"
            onClick={() => onFork(event)}
            type="button"
          >
            {t.timeMachine.forkHere}
          </button>
        </div>
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-gray-400">
            {t.timeMachine.rawPayload}
          </summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-white p-3 text-xs leading-5 text-gray-600 ring-1 ring-gray-200">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        </details>
      </article>
    </li>
  );
}

function ForkModal({
  draft,
  isSubmitting,
  onChange,
  onClose,
  onSubmit,
}: {
  draft: ForkDraft;
  isSubmitting: boolean;
  onChange: (draft: ForkDraft) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const { t } = useLanguage();
  const copy = t.timeMachine;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/50 px-4 py-6 backdrop-blur-sm">
      <div className="w-full max-w-2xl rounded-xl bg-white shadow-2xl">
        <form onSubmit={onSubmit}>
          <div className="border-b border-gray-200 px-5 py-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">
              {copy.forkTimeline}
            </p>
            <h2 className="mt-1 text-xl font-semibold text-gray-950">
              {copy.forkMoment}
            </h2>
            <p className="mt-2 text-sm leading-6 text-gray-500">
              {copy.rollbackBefore(formatLocalDateTime(draft.event.timestamp))}
            </p>
          </div>

          <div className="space-y-4 px-5 py-5">
            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                new_branch_name
              </span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-cyan-500 focus:bg-white focus:ring-4 focus:ring-cyan-100"
                onChange={(event) =>
                  onChange({ ...draft, newBranchName: event.target.value })
                }
                placeholder="universe_beta_no_allergy"
                value={draft.newBranchName}
              />
            </label>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                counterfactual_event
              </span>
              <textarea
                className="mt-2 min-h-40 w-full resize-y rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm leading-6 text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-cyan-500 focus:bg-white focus:ring-4 focus:ring-cyan-100"
                onChange={(event) =>
                  onChange({
                    ...draft,
                    counterfactualEvent: event.target.value,
                  })
                }
                placeholder={copy.counterfactualPlaceholder}
                value={draft.counterfactualEvent}
              />
            </label>
          </div>

          <div className="flex flex-col-reverse gap-3 border-t border-gray-200 px-5 py-4 sm:flex-row sm:justify-end">
            <button
              className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isSubmitting}
              onClick={onClose}
              type="button"
            >
              {t.common.cancel}
            </button>
            <button
              className="inline-flex items-center justify-center gap-2 rounded-full bg-gray-950 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isSubmitting}
              type="submit"
            >
              {isSubmitting ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              ) : null}
              {isSubmitting ? copy.forking : copy.createFork}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function TimelineSkeleton() {
  return (
    <div className="space-y-4">
      {[0, 1, 2].map((item) => (
        <div
          className="rounded-xl border border-gray-200 bg-gray-50 p-4"
          key={item}
        >
          <div className="h-4 w-44 animate-pulse rounded-full bg-gray-200" />
          <div className="mt-4 h-3 w-full animate-pulse rounded-full bg-gray-200" />
          <div className="mt-2 h-3 w-2/3 animate-pulse rounded-full bg-gray-200" />
        </div>
      ))}
    </div>
  );
}

function EmptyTimeline() {
  const { t } = useLanguage();

  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-5 py-10 text-center">
      <h3 className="text-base font-semibold text-gray-950">
        {t.timeMachine.emptyTitle}
      </h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-gray-500">
        {t.timeMachine.emptyHelp}
      </p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
      <dt className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        {label}
      </dt>
      <dd className="mt-1 break-words text-sm font-semibold text-gray-950">
        {value}
      </dd>
    </div>
  );
}

function parseCounterfactualEvent(rawValue: string) {
  const trimmed = rawValue.trim();
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (
      parsed &&
      typeof parsed === "object" &&
      !Array.isArray(parsed)
    ) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Plain-language counterfactuals are wrapped below.
  }

  return {
    event_type: "COUNTERFACTUAL_EVENT",
    description: trimmed,
  };
}

function summarizeEvent(event: EventLog, copy: Dictionary["timeMachine"]) {
  const payload = event.payload ?? {};

  switch (event.event_type) {
    case "MESSAGE_RECEIVED":
    case "CHAT_TURN_RECORDED":
      return compactLines([
        payload.user_message
          ? `${copy.eventUser}: ${String(payload.user_message)}`
          : "",
        payload.agent_reply
          ? `${copy.eventAgent}: ${String(payload.agent_reply)}`
          : "",
      ], copy.eventNoSummary);
    case "POST_CREATED":
      return String(payload.content ?? copy.eventPostCreated);
    case "FEEDBACK_CREATED":
      return compactLines([
        payload.original_text
          ? `${copy.eventOriginal}: ${String(payload.original_text)}`
          : "",
        payload.corrected_text
          ? `${copy.eventCorrected}: ${String(payload.corrected_text)}`
          : "",
      ], copy.eventNoSummary);
    case "RELATIONSHIP_CHANGED":
      return copy.eventRelationshipChanged(
        String(payload.target_agent_id ?? "?"),
        String(payload.affinity_change ?? "?"),
        String(payload.affinity_score ?? "?"),
      );
    case "CORE_MEMORY_UPDATED":
      return copy.eventCoreMemoryUpdated;
    case "WORKING_MEMORY_CLEARED":
      return copy.eventWorkingMemoryCleared;
    case "AGENT_CREATED":
      return copy.eventAgentCreated;
    case "AGENT_PROFILE_UPDATED":
      return copy.eventProfileUpdated;
    default:
      return JSON.stringify(payload);
  }
}

function compactLines(lines: string[], emptyText: string) {
  const compacted = lines.filter(Boolean).join("\n");
  return compacted || emptyText;
}

function normalizeBranches(result: unknown) {
  const rawBranches = Array.isArray(result)
    ? result
    : result && typeof result === "object"
      ? "branch_ids" in result
        ? (result as { branch_ids?: unknown }).branch_ids
        : "branches" in result
          ? (result as { branches?: unknown }).branches
          : undefined
      : undefined;

  const branchIds = Array.isArray(rawBranches)
    ? rawBranches
        .map((item) => {
          if (typeof item === "string") {
            return item;
          }
          if (item && typeof item === "object" && "branch_id" in item) {
            return String((item as { branch_id: unknown }).branch_id);
          }
          return "";
        })
        .map((branchId) => branchId.trim())
        .filter(Boolean)
    : [];

  return Array.from(new Set(["main", ...branchIds])).sort((left, right) => {
    if (left === "main") {
      return -1;
    }
    if (right === "main") {
      return 1;
    }
    return left.localeCompare(right);
  });
}

function groupEventsByDay(events: EventLog[]) {
  const groups: Array<{ key: string; label: string; events: EventLog[] }> = [];
  const groupByKey = new Map<string, { key: string; label: string; events: EventLog[] }>();

  for (const event of events) {
    const key = dateKeyFromTimestamp(event.timestamp);
    let group = groupByKey.get(key);
    if (!group) {
      group = {
        key,
        label: formatDateGroupLabel(event.timestamp),
        events: [],
      };
      groupByKey.set(key, group);
      groups.push(group);
    }
    group.events.push(event);
  }

  return groups;
}

function latestEventDateKey(events: EventLog[]) {
  if (events.length === 0) {
    return new Set<string>();
  }
  const latestEvent = [...events].sort((left, right) => {
    const byTime =
      parseUtcTimestamp(right.timestamp).getTime() -
      parseUtcTimestamp(left.timestamp).getTime();
    return byTime === 0 ? right.event_id - left.event_id : byTime;
  })[0];
  return new Set([dateKeyFromTimestamp(latestEvent.timestamp)]);
}

function dateKeyFromTimestamp(timestamp: string) {
  const date = parseUtcTimestamp(timestamp);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatDateGroupLabel(timestamp: string) {
  return parseUtcTimestamp(timestamp).toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function formatBranchTimestamp(timestamp: string) {
  const date = parseUtcTimestamp(timestamp);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${year}${month}${day}_${hours}${minutes}${seconds}`;
}
