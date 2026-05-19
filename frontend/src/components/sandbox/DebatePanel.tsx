"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  AgentDirectoryEntry,
  DebateTriggerResponse,
  getAgentsDirectory,
  triggerDebate,
} from "@/lib/api";
import { useUiLanguage } from "@/lib/i18n";
import { dictionary } from "@/locales/dictionary";

type DebatePanelProps = {
  branchId: string;
};

const DEFAULT_MAX_TURNS = 10;

export function DebatePanel({ branchId }: DebatePanelProps) {
  const { language } = useUiLanguage();
  const t = dictionary[language].sandbox;
  const [topic, setTopic] = useState("");
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>([]);
  const [agents, setAgents] = useState<AgentDirectoryEntry[]>([]);
  const [maxTurns, setMaxTurns] = useState(DEFAULT_MAX_TURNS);
  const [result, setResult] = useState<DebateTriggerResponse | null>(null);
  const [error, setError] = useState("");
  const [directoryError, setDirectoryError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingAgents, setIsLoadingAgents] = useState(false);

  useEffect(() => {
    async function loadAgents() {
      setIsLoadingAgents(true);
      setDirectoryError("");
      try {
        const directory = await getAgentsDirectory();
        setAgents(directory);
      } catch (err) {
        setDirectoryError(
          t.agentDirectoryFailed(err instanceof Error ? err.message : undefined),
        );
      } finally {
        setIsLoadingAgents(false);
      }
    }

    void loadAgents();
  }, [t]);

  function toggleAgent(agentId: string) {
    setSelectedAgentIds((currentAgentIds) =>
      currentAgentIds.includes(agentId)
        ? currentAgentIds.filter((selectedId) => selectedId !== agentId)
        : [...currentAgentIds, agentId],
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setResult(null);

    const normalizedTopic = topic.trim();
    if (!normalizedTopic) {
      setError(t.topicRequired);
      return;
    }
    if (selectedAgentIds.length === 0) {
      setError(t.agentRequired);
      return;
    }

    setIsLoading(true);
    try {
      const debateResult = await triggerDebate({
        topic: normalizedTopic,
        participant_agent_ids: selectedAgentIds,
        branch_id: branchId,
        max_turns: Math.min(Math.max(maxTurns, 1), 20),
      });
      setResult(debateResult);
    } catch (err) {
      setError(t.debateFailed(err instanceof Error ? err.message : undefined));
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-1">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">
          Phase 1
        </p>
        <h2 className="text-xl font-semibold tracking-tight text-gray-950">
          {t.debatePanelTitle}
        </h2>
        <p className="text-sm leading-6 text-gray-600">
          {t.debatePanelSubtitle}
        </p>
      </div>

      <form className="mt-5 grid gap-4" onSubmit={handleSubmit}>
        <label className="block">
          <span className="text-sm font-semibold text-gray-700">
            {t.debateTopic}
          </span>
          <input
            className="mt-2 w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
            onChange={(event) => setTopic(event.target.value)}
            placeholder={t.debateTopicPlaceholder}
            value={topic}
          />
        </label>

        <div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm font-semibold text-gray-700">
              {t.participantAgents}
            </span>
            <span className="text-xs font-medium text-gray-500">
              {isLoadingAgents ? t.loadingAgents : t.selectedAgents(selectedAgentIds.length)}
            </span>
          </div>
          <div className="mt-2 max-h-56 overflow-auto rounded-xl border border-gray-200 bg-gray-50 p-3">
            {agents.length === 0 ? (
              <p className="px-2 py-3 text-sm text-gray-500">
                {isLoadingAgents ? t.loadingAgents : t.noAgents}
              </p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {agents.map((agent) => (
                  <label
                    className="flex cursor-pointer items-center gap-3 rounded-lg bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-indigo-50"
                    key={agent.agent_id}
                  >
                    <input
                      checked={selectedAgentIds.includes(agent.agent_id)}
                      className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                      onChange={() => toggleAgent(agent.agent_id)}
                      type="checkbox"
                    />
                    <span className="min-w-0 truncate">
                      {agent.name} #{agent.agent_id}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
          {directoryError ? (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-800">
              {directoryError}
            </div>
          ) : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
          <label className="block">
            <span className="text-sm font-semibold text-gray-700">
              {t.maxTurns}
            </span>
            <input
              className="mt-2 w-full rounded-xl border border-gray-200 px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
              max={20}
              min={1}
              onChange={(event) => setMaxTurns(Number(event.target.value) || 1)}
              type="number"
              value={maxTurns}
            />
          </label>
          <button
            className="inline-flex h-11 items-center justify-center rounded-xl bg-gray-950 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isLoading}
            type="submit"
          >
            {isLoading ? t.debateRunning : t.triggerDebate}
          </button>
        </div>
      </form>

      {error ? (
        <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700">
          {error}
        </div>
      ) : null}

      {result ? (
        <div className="mt-5 rounded-xl border border-indigo-100 bg-indigo-50 p-4">
          <div className="grid gap-3 text-sm text-indigo-950 sm:grid-cols-3">
            <Metric label={t.metricStatus} value={result.status} />
            <Metric label={t.metricTurns} value={result.turns_executed} />
            <Metric
              label={t.metricConsensus}
              value={result.consensus_reached ? t.yes : t.no}
            />
          </div>
          <pre className="mt-4 max-h-80 overflow-auto rounded-xl bg-white/80 p-4 text-xs leading-5 text-gray-800">
            {formatFinalReport(result.final_report)}
          </pre>
        </div>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl bg-white/70 px-3 py-2">
      <p className="text-xs font-semibold text-indigo-600">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-gray-950">
        {value}
      </p>
    </div>
  );
}

function formatFinalReport(report: DebateTriggerResponse["final_report"]) {
  if (typeof report === "string") {
    return report;
  }
  return JSON.stringify(report, null, 2);
}
