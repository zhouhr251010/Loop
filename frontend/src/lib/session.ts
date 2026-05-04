"use client";

export type LoopSession = {
  user_id: number;
  username: string;
  agent_name?: string;
};

const SESSION_KEY = "loop_session";

export function saveSession(session: LoopSession) {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function loadSession(): LoopSession | null {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw) as LoopSession;
  } catch {
    localStorage.removeItem(SESSION_KEY);
    return null;
  }
}

export function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}
