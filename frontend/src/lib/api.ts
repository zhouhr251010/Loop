import { getAccessToken } from "@/lib/session";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type User = {
  id: number;
  username: string;
  mbti_type?: string | null;
  big_five_scores?: Record<string, number> | null;
  schwartz_values?: Record<string, number> | null;
  autobiography?: string | null;
  core_memory?: Record<string, unknown> | null;
};

export type Agent = {
  id: number;
  user_id: number;
  agent_name: string;
  system_prompt_base: string;
};

export type Post = {
  id: number;
  agent_id: number;
  agent_name: string;
  content: string;
  timestamp: string;
  branch_id?: string;
  is_corrected?: boolean;
};

export type PostOut = Omit<Post, "agent_name">;

export type HealthResponse = {
  status: string;
  service: string;
};

export type ChatReply = {
  reply: string;
  memory_chunks_used: number;
  model_used: "fast" | "deep";
  stored: boolean;
  warning?: string | null;
  chat_log: {
    id: number;
    agent_id: number;
    user_message: string;
    agent_reply: string;
    timestamp: string;
  } | null;
};

export type MemoryUploadResponse = {
  message: string;
  chunks_added: number;
};

export type ImportedChatMessage = {
  sender_agent_id: number;
  content: string;
  timestamp?: string | null;
};

export type ChatImportResponse = {
  message: string;
  target_agent_id: number;
  records_received: number;
  chunks_added: number;
  me_messages: number;
  others_messages: number;
};

export type MemorySearchResponse = {
  query: string;
  chunks: string[];
};

export type RelationshipUpdate = {
  target_agent_id: number;
  affinity_change: number;
  affinity_score: number;
};

export type MemoryConsolidationResponse = {
  message: string;
  user_id: number;
  agent_id: number;
  records_consolidated: number;
  chunks_added: number;
  graph_triples_extracted?: number;
  daily_events_created?: number;
  high_level_insights_created?: number;
  core_memory_updated?: boolean;
  relationship_updates: RelationshipUpdate[];
  graph_memory_cleared: boolean;
};

export type AgentWorkingMemoryState = {
  agent_id: number;
  branch_id: string;
  graph_available: boolean;
  message_count: number;
  working_message_count: number;
  core_memory?: Record<string, unknown>;
  current_core_memory?: string;
  summary: string;
  active_topic?: string;
  topic_count?: number;
  topic_message_counts?: Record<string, number>;
  topic_summaries?: Record<string, string>;
  emotion: string;
  energy: number;
  error?: string | null;
};

export type Relationship = {
  target_agent_id: number;
  target_agent_name: string;
  affinity_score: number;
};

export type PersonalizedPostPreview = {
  id: number;
  agent_id: number;
  agent_name: string;
  affinity_score: number;
  content: string;
  timestamp: string;
};

export type AuthSession = {
  user: User;
  access_token: string;
  token_type: "bearer";
  expires_in: number;
};

export type AgentSessionChoice = {
  user: User;
  agent: Agent;
};

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const rawMessage = await response.text();
    let message = rawMessage;

    try {
      const parsed = JSON.parse(rawMessage) as { detail?: unknown };
      if (typeof parsed.detail === "string") {
        message = parsed.detail;
      } else if (parsed.detail) {
        message = JSON.stringify(parsed.detail);
      }
    } catch {
      message = rawMessage;
    }

    throw new Error(message || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}
