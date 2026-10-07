import { timestampMs } from "./time";

export type HomeLaunchVariant = "none" | "first_visit" | "new_project" | "returning_soft";

const HOME_LAUNCH_STATE_KEY = "pawtale_home_launch_state";
const HOME_LAUNCH_STATE_VERSION = 1;
const DEFAULT_SOFT_RETURN_MS = 14 * 24 * 60 * 60 * 1000;

interface PendingNewProjectLaunch {
  project_id: string;
  project_title: string;
  marked_at: string;
}

export interface HomeLaunchUserState {
  first_seen_at?: string;
  last_opened_at?: string;
  last_soft_launch_at?: string;
  book_opened_at?: string;
  pending_new_project?: PendingNewProjectLaunch;
}

interface HomeLaunchStore {
  version: number;
  users: Record<string, HomeLaunchUserState>;
}

export interface HomeLaunchDecisionInput {
  state?: HomeLaunchUserState;
  projectId?: string;
  hasProject?: boolean;
  nowMs?: number;
  softReturnMs?: number;
}

export interface HomeLaunchDecision {
  variant: HomeLaunchVariant;
  state: HomeLaunchUserState;
}

export interface HomeLaunchProjectMarker {
  userId: string;
  projectId: string;
  projectTitle?: string;
  nowMs?: number;
}

function storageApi(): Pick<WechatMiniprogram.Wx, "getStorageSync" | "setStorageSync"> | null {
  return typeof wx === "undefined" ? null : wx;
}

function isoTime(nowMs: number): string {
  return new Date(nowMs).toISOString();
}

function normalizeStore(value: unknown): HomeLaunchStore {
  if (!value || typeof value !== "object") {
    return { version: HOME_LAUNCH_STATE_VERSION, users: {} };
  }
  const store = value as Partial<HomeLaunchStore>;
  if (store.version !== HOME_LAUNCH_STATE_VERSION || !store.users || typeof store.users !== "object") {
    return { version: HOME_LAUNCH_STATE_VERSION, users: {} };
  }
  return {
    version: HOME_LAUNCH_STATE_VERSION,
    users: { ...store.users },
  };
}

function readStore(): HomeLaunchStore {
  const storage = storageApi();
  if (!storage) {
    return { version: HOME_LAUNCH_STATE_VERSION, users: {} };
  }
  try {
    return normalizeStore(storage.getStorageSync(HOME_LAUNCH_STATE_KEY));
  } catch (error) {
    console.warn("read home launch state failed", error);
    return { version: HOME_LAUNCH_STATE_VERSION, users: {} };
  }
}

function writeStore(store: HomeLaunchStore): void {
  const storage = storageApi();
  if (!storage) {
    return;
  }
  try {
    storage.setStorageSync(HOME_LAUNCH_STATE_KEY, store);
  } catch (error) {
    console.warn("write home launch state failed", error);
  }
}

function userState(store: HomeLaunchStore, userId: string): HomeLaunchUserState {
  return { ...(store.users[userId] || {}) };
}

function returningSoftDue(
  state: HomeLaunchUserState,
  nowMs: number,
  softReturnMs: number,
): boolean {
  const lastOpenedMs = timestampMs(state.last_opened_at);
  if (!lastOpenedMs || nowMs - lastOpenedMs < softReturnMs) {
    return false;
  }
  const lastSoftLaunchMs = timestampMs(state.last_soft_launch_at);
  return !lastSoftLaunchMs || lastSoftLaunchMs < lastOpenedMs;
}

export function decideHomeLaunch(input: HomeLaunchDecisionInput): HomeLaunchDecision {
  const nowMs = input.nowMs ?? Date.now();
  const now = isoTime(nowMs);
  const state: HomeLaunchUserState = { ...(input.state || {}) };
  const projectId = input.projectId || "";
  let variant: HomeLaunchVariant = "none";

  if (state.pending_new_project?.project_id && state.pending_new_project.project_id === projectId) {
    variant = "new_project";
  } else if (!state.first_seen_at) {
    variant = "first_visit";
  } else if (input.hasProject && returningSoftDue(state, nowMs, input.softReturnMs ?? DEFAULT_SOFT_RETURN_MS)) {
    variant = "returning_soft";
  }

  const nextState: HomeLaunchUserState = {
    ...state,
    first_seen_at: state.first_seen_at || now,
    last_opened_at: now,
  };
  if (variant === "returning_soft") {
    nextState.last_soft_launch_at = now;
  }

  return { variant, state: nextState };
}

export function resolveHomeLaunch(
  userId: string,
  input: Omit<HomeLaunchDecisionInput, "state">,
): HomeLaunchVariant {
  if (!userId) {
    return "none";
  }
  const store = readStore();
  const decision = decideHomeLaunch({
    ...input,
    state: userState(store, userId),
  });
  writeStore({
    ...store,
    users: {
      ...store.users,
      [userId]: decision.state,
    },
  });
  return decision.variant;
}

export function markHomeLaunchProjectCreated(marker: HomeLaunchProjectMarker): void {
  if (!marker.userId || !marker.projectId) {
    return;
  }
  const store = readStore();
  const state = userState(store, marker.userId);
  writeStore({
    ...store,
    users: {
      ...store.users,
      [marker.userId]: {
        ...state,
        pending_new_project: {
          project_id: marker.projectId,
          project_title: marker.projectTitle || "",
          marked_at: isoTime(marker.nowMs ?? Date.now()),
        },
      },
    },
  });
}

export function dismissHomeLaunch(userId: string, variant: HomeLaunchVariant, projectId?: string): void {
  if (!userId || variant !== "new_project") {
    return;
  }
  const store = readStore();
  const state = userState(store, userId);
  if (!state.pending_new_project) {
    return;
  }
  if (projectId && state.pending_new_project.project_id !== projectId) {
    return;
  }
  const nextState: HomeLaunchUserState = { ...state };
  delete nextState.pending_new_project;
  writeStore({
    ...store,
    users: {
      ...store.users,
      [userId]: nextState,
    },
  });
}

export function homeBookOpened(userId: string): boolean {
  return Boolean(userId && userState(readStore(), userId).book_opened_at);
}

export function markHomeBookOpened(userId: string, nowMs = Date.now()): void {
  if (!userId) {
    return;
  }
  const store = readStore();
  const state = userState(store, userId);
  if (state.book_opened_at) {
    return;
  }
  writeStore({
    ...store,
    users: {
      ...store.users,
      [userId]: { ...state, book_opened_at: isoTime(nowMs) },
    },
  });
}
