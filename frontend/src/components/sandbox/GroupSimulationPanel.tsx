"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  AgentDirectoryEntry,
  GroupEntityType,
  GroupResponse,
  GroupTickResponse,
  GroupType,
  UserDirectoryEntry,
  addGroupMember,
  createGroup,
  getAgentsDirectory,
  getUsersDirectory,
  triggerGroupTick,
} from "@/lib/api";
import { useUiLanguage } from "@/lib/i18n";
import { dictionary } from "@/locales/dictionary";

type GroupSimulationPanelProps = {
  branchId: string;
};

export function GroupSimulationPanel({ branchId }: GroupSimulationPanelProps) {
  const { language } = useUiLanguage();
  const t = dictionary[language].sandbox;
  const [groupName, setGroupName] = useState("");
  const [groupTopic, setGroupTopic] = useState("");
  const [groupType, setGroupType] = useState<GroupType>("AGENT_ONLY");
  const [createdGroup, setCreatedGroup] = useState<GroupResponse | null>(null);
  const [memberGroupId, setMemberGroupId] = useState("");
  const [entityId, setEntityId] = useState("");
  const [entityType, setEntityType] = useState<GroupEntityType>("AGENT");
  const [tickGroupId, setTickGroupId] = useState("");
  const [agents, setAgents] = useState<AgentDirectoryEntry[]>([]);
  const [users, setUsers] = useState<UserDirectoryEntry[]>([]);
  const [tickResult, setTickResult] = useState<GroupTickResponse | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [directoryError, setDirectoryError] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [isAddingMember, setIsAddingMember] = useState(false);
  const [isTicking, setIsTicking] = useState(false);
  const [isLoadingDirectories, setIsLoadingDirectories] = useState(false);

  useEffect(() => {
    async function loadDirectories() {
      setIsLoadingDirectories(true);
      setDirectoryError("");
      try {
        const [agentDirectory, userDirectory] = await Promise.all([
          getAgentsDirectory(),
          getUsersDirectory(),
        ]);
        setAgents(agentDirectory);
        setUsers(userDirectory);
      } catch (err) {
        const message = err instanceof Error ? err.message : undefined;
        setDirectoryError(
          `${t.agentDirectoryFailed(message)} ${t.userDirectoryFailed(message)}`,
        );
      } finally {
        setIsLoadingDirectories(false);
      }
    }

    void loadDirectories();
  }, [t]);

  const currentEntityOptions = useMemo(
    () =>
      entityType === "AGENT"
        ? agents.map((agent) => ({
            id: agent.agent_id,
            label: `${agent.name} #${agent.agent_id}`,
          }))
        : users.map((user) => ({
            id: user.user_id,
            label: `${user.username} #${user.user_id}`,
          })),
    [agents, entityType, users],
  );

  async function handleCreateGroup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setCreatedGroup(null);

    const normalizedName = groupName.trim();
    if (!normalizedName) {
      setError(t.groupNameRequired);
      return;
    }

    setIsCreating(true);
    try {
      const group = await createGroup({
        name: normalizedName,
        topic: groupTopic.trim() || undefined,
        group_type: groupType,
      });
      setCreatedGroup(group);
      setMemberGroupId(group.id);
      setTickGroupId(group.id);
      setMessage(t.groupCreated(group.id));
    } catch (err) {
      setError(t.createGroupFailed(err instanceof Error ? err.message : undefined));
    } finally {
      setIsCreating(false);
    }
  }

  async function handleAddMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");

    const normalizedGroupId = memberGroupId.trim();
    const normalizedEntityId = entityId.trim();
    if (!normalizedGroupId || !normalizedEntityId) {
      setError(t.groupAndEntityRequired);
      return;
    }

    setIsAddingMember(true);
    try {
      const member = await addGroupMember(normalizedGroupId, {
        entity_id: normalizedEntityId,
        entity_type: entityType,
      });
      setMessage(
        t.memberAdded(member.entity_type, member.entity_id, member.group_id),
      );
    } catch (err) {
      setError(t.addMemberFailed(err instanceof Error ? err.message : undefined));
    } finally {
      setIsAddingMember(false);
    }
  }

  async function handleTickGroup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setTickResult(null);

    const normalizedGroupId = tickGroupId.trim();
    if (!normalizedGroupId) {
      setError(t.groupIdRequired);
      return;
    }

    setIsTicking(true);
    try {
      const result = await triggerGroupTick(normalizedGroupId, branchId);
      setTickResult(result);
      setMessage(t.tickComplete);
    } catch (err) {
      setError(t.tickFailed(err instanceof Error ? err.message : undefined));
    } finally {
      setIsTicking(false);
    }
  }

  const speaker =
    tickResult?.current_speaker ?? tickResult?.speaker_agent_id ?? t.noSpeaker;
  const content =
    typeof tickResult?.content === "string"
      ? tickResult.content
      : typeof tickResult?.message === "string"
        ? tickResult.message
        : "";

  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-1">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-fuchsia-600">
          Phase 3
        </p>
        <h2 className="text-xl font-semibold tracking-tight text-gray-950">
          {t.groupPanelTitle}
        </h2>
        <p className="text-sm leading-6 text-gray-600">
          {t.groupPanelSubtitle}
        </p>
      </div>

      <div className="mt-5 divide-y divide-gray-200">
        <form className="pb-5" onSubmit={handleCreateGroup}>
          <h3 className="text-sm font-semibold text-gray-950">{t.createGroup}</h3>
          <div className="mt-4 grid gap-3">
            <label className="block">
              <span className="text-sm font-semibold text-gray-700">
                {t.groupName}
              </span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-fuchsia-400 focus:ring-4 focus:ring-fuchsia-100"
                onChange={(event) => setGroupName(event.target.value)}
                placeholder={t.groupNamePlaceholder}
                value={groupName}
              />
            </label>
            <label className="block">
              <span className="text-sm font-semibold text-gray-700">
                {t.groupTopic}
              </span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-fuchsia-400 focus:ring-4 focus:ring-fuchsia-100"
                onChange={(event) => setGroupTopic(event.target.value)}
                placeholder={t.groupTopicPlaceholder}
                value={groupTopic}
              />
            </label>
            <label className="block">
              <span className="text-sm font-semibold text-gray-700">
                {t.groupType}
              </span>
              <select
                className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-semibold text-gray-950 outline-none transition focus:border-fuchsia-400 focus:ring-4 focus:ring-fuchsia-100"
                onChange={(event) => setGroupType(event.target.value as GroupType)}
                value={groupType}
              >
                <option value="AGENT_ONLY">AGENT_ONLY</option>
                <option value="HUMAN_ONLY">HUMAN_ONLY</option>
              </select>
            </label>
          </div>
          <button
            className="mt-4 inline-flex h-10 items-center justify-center rounded-xl bg-gray-950 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isCreating}
            type="submit"
          >
            {isCreating ? t.creating : t.createGroup}
          </button>
          {createdGroup ? (
            <p className="mt-3 break-all rounded-lg bg-gray-50 px-3 py-2 text-xs font-medium text-gray-700">
              Group ID: {createdGroup.id}
            </p>
          ) : null}
        </form>

        <form className="py-5" onSubmit={handleAddMember}>
          <h3 className="text-sm font-semibold text-gray-950">
            {t.memberManagement}
          </h3>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <label className="block sm:col-span-3">
              <span className="text-sm font-semibold text-gray-700">Group ID</span>
              <input
                className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-fuchsia-400 focus:ring-4 focus:ring-fuchsia-100"
                onChange={(event) => setMemberGroupId(event.target.value)}
                value={memberGroupId}
              />
            </label>
            <label className="block">
              <span className="text-sm font-semibold text-gray-700">
                {t.entityType}
              </span>
              <select
                className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-semibold text-gray-950 outline-none transition focus:border-fuchsia-400 focus:ring-4 focus:ring-fuchsia-100"
                onChange={(event) => {
                  setEntityType(event.target.value as GroupEntityType);
                  setEntityId("");
                }}
                value={entityType}
              >
                <option value="AGENT">AGENT</option>
                <option value="USER">USER</option>
              </select>
            </label>
            <label className="block sm:col-span-2">
              <span className="text-sm font-semibold text-gray-700">
                {t.entity}
              </span>
              <select
                className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-fuchsia-400 focus:ring-4 focus:ring-fuchsia-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={isLoadingDirectories || currentEntityOptions.length === 0}
                onChange={(event) => setEntityId(event.target.value)}
                value={entityId}
              >
                <option disabled value="">
                  {isLoadingDirectories
                    ? entityType === "AGENT"
                      ? t.loadingAgents
                      : t.usersLoading
                    : t.chooseEntity}
                </option>
                {currentEntityOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {directoryError ? (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-800">
              {directoryError}
            </div>
          ) : null}
          <button
            className="mt-4 inline-flex h-10 items-center justify-center rounded-xl bg-gray-950 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isAddingMember}
            type="submit"
          >
            {isAddingMember ? t.adding : t.addMember}
          </button>
        </form>

        <form className="pt-5" onSubmit={handleTickGroup}>
          <h3 className="text-sm font-semibold text-gray-950">{t.tickGroup}</h3>
          <label className="mt-4 block">
            <span className="text-sm font-semibold text-gray-700">Group ID</span>
            <input
              className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-950 outline-none transition focus:border-fuchsia-400 focus:ring-4 focus:ring-fuchsia-100"
              onChange={(event) => setTickGroupId(event.target.value)}
              value={tickGroupId}
            />
            <span className="mt-1 block text-xs text-gray-500">
              {t.tickGroupTodo}
            </span>
          </label>
          <button
            className="mt-4 inline-flex h-10 items-center justify-center rounded-xl bg-gray-950 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isTicking}
            type="submit"
          >
            {isTicking ? t.ticking : t.tickGroup}
          </button>
        </form>
      </div>

      {message ? (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700">
          {message}
        </div>
      ) : null}
      {error ? (
        <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700">
          {error}
        </div>
      ) : null}

      {tickResult ? (
        <div className="mt-5 rounded-xl border border-fuchsia-100 bg-fuchsia-50 p-4">
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <Metric label={t.speaker} value={String(speaker)} />
            <Metric label={t.metricStatus} value={String(tickResult.status ?? "completed")} />
          </div>
          {content ? (
            <p className="mt-4 whitespace-pre-wrap rounded-xl bg-white/80 p-4 text-sm leading-6 text-gray-800">
              {content}
            </p>
          ) : (
            <pre className="mt-4 max-h-72 overflow-auto rounded-xl bg-white/80 p-4 text-xs leading-5 text-gray-800">
              {JSON.stringify(tickResult, null, 2)}
            </pre>
          )}
        </div>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-white/70 px-3 py-2">
      <p className="text-xs font-semibold text-fuchsia-600">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-gray-950">
        {value}
      </p>
    </div>
  );
}
