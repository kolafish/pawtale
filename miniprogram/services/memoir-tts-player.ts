import type { MemoirTtsAsset, MemoirTtsSourceType } from "../domain/memoir";
import {
  createMemoirTtsAsset,
  processMemoirTtsJob,
  type MemoirTtsAssetInput,
} from "./memoir";

export const MEMOIR_TTS_PLAYBACK_SPEEDS = [0.75, 1, 1.25, 1.5, 2] as const;

export type MemoirTtsPlaybackStatus =
  | "idle"
  | "loading"
  | "generating"
  | "ready"
  | "playing"
  | "paused"
  | "error";

export interface MemoirTtsPlaybackSource {
  source_type: MemoirTtsSourceType;
  source_id: string;
  label?: string;
}

export interface MemoirTtsPlaybackState {
  status: MemoirTtsPlaybackStatus;
  source_key: string;
  source_label: string;
  speed: number;
  asset?: MemoirTtsAsset;
  temp_url: string;
  position_seconds: number;
  duration_seconds: number;
  message: string;
  error: string;
}

type MemoirTtsPlaybackListener = (state: MemoirTtsPlaybackState) => void;

let audioContext: WechatMiniprogram.InnerAudioContext | null = null;
const listeners = new Set<MemoirTtsPlaybackListener>();
let prepareSequence = 0;

let state: MemoirTtsPlaybackState = {
  status: "idle",
  source_key: "",
  source_label: "",
  speed: 1,
  temp_url: "",
  position_seconds: 0,
  duration_seconds: 0,
  message: "",
  error: "",
};

export function memoirTtsSourceKey(source: MemoirTtsPlaybackSource): string {
  return `${source.source_type}:${source.source_id}`;
}

export function normalizeMemoirTtsPlaybackSpeed(speed: number): number {
  if (!Number.isFinite(speed)) {
    return 1;
  }
  return MEMOIR_TTS_PLAYBACK_SPEEDS.reduce((closest, candidate) => {
    return Math.abs(candidate - speed) < Math.abs(closest - speed) ? candidate : closest;
  }, 1);
}

export function nextMemoirTtsPlaybackSpeed(currentSpeed: number): number {
  const normalized = normalizeMemoirTtsPlaybackSpeed(currentSpeed);
  const index = MEMOIR_TTS_PLAYBACK_SPEEDS.indexOf(normalized as (typeof MEMOIR_TTS_PLAYBACK_SPEEDS)[number]);
  return MEMOIR_TTS_PLAYBACK_SPEEDS[(index + 1) % MEMOIR_TTS_PLAYBACK_SPEEDS.length];
}

function emit(nextState: MemoirTtsPlaybackState): MemoirTtsPlaybackState {
  state = nextState;
  listeners.forEach((listener) => listener(state));
  return state;
}

function patchState(patch: Partial<MemoirTtsPlaybackState>): MemoirTtsPlaybackState {
  return emit({
    ...state,
    ...patch,
  });
}

export function readMemoirTtsPlaybackState(): MemoirTtsPlaybackState {
  return { ...state };
}

export function subscribeMemoirTtsPlayback(listener: MemoirTtsPlaybackListener): () => void {
  listeners.add(listener);
  listener(readMemoirTtsPlaybackState());
  return () => {
    listeners.delete(listener);
  };
}

function setAudioPlaybackSpeed(speed: number): void {
  if (audioContext) {
    audioContext.playbackRate = speed;
  }
}

function ensureAudioContext(): WechatMiniprogram.InnerAudioContext {
  if (audioContext) {
    return audioContext;
  }
  audioContext = wx.createInnerAudioContext();
  audioContext.onPlay(() => {
    patchState({ status: "playing", message: "", error: "" });
  });
  audioContext.onPause(() => {
    patchState({ status: "paused" });
  });
  audioContext.onStop(() => {
    patchState({ status: state.temp_url ? "ready" : "idle", position_seconds: 0 });
  });
  audioContext.onEnded(() => {
    patchState({ status: "ready", position_seconds: 0 });
  });
  audioContext.onTimeUpdate(() => {
    if (!audioContext) {
      return;
    }
    patchState({
      position_seconds: Math.max(0, Math.round(audioContext.currentTime || 0)),
      duration_seconds: Math.max(0, Math.round(audioContext.duration || 0)),
    });
  });
  audioContext.onError((error) => {
    patchState({
      status: "error",
      message: "",
      error: error.errMsg || "语音播放失败",
    });
  });
  audioContext.playbackRate = state.speed;
  return audioContext;
}

function getTempFileUrl(fileId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.cloud.getTempFileURL({
      fileList: [fileId],
      success: (result) => {
        const file = result.fileList[0];
        if (file?.tempFileURL) {
          resolve(file.tempFileURL);
          return;
        }
        reject(new Error("朗读音频地址生成失败"));
      },
      fail: (error) => {
        reject(new Error(error.errMsg || "朗读音频地址生成失败"));
      },
    });
  });
}

export async function prepareMemoirTtsPlayback(
  source: MemoirTtsPlaybackSource,
  options: Omit<MemoirTtsAssetInput, "source_type" | "source_id"> = {},
): Promise<MemoirTtsPlaybackState> {
  const sourceKey = memoirTtsSourceKey(source);
  const sequence = prepareSequence + 1;
  prepareSequence = sequence;
  patchState({
    status: "loading",
    source_key: sourceKey,
    source_label: source.label || "",
    asset: undefined,
    temp_url: "",
    position_seconds: 0,
    duration_seconds: 0,
    message: "正在准备朗读",
    error: "",
  });

  try {
    const created = await createMemoirTtsAsset({
      source_type: source.source_type,
      source_id: source.source_id,
      ...options,
    });
    let asset = created.asset || null;
    if (!asset && created.job) {
      patchState({ status: "generating", message: "正在生成朗读音频" });
      const job = await processMemoirTtsJob(created.job.id);
      if (job.status !== "completed" || !job.asset) {
        throw new Error(job.error_message || "朗读生成失败");
      }
      asset = job.asset;
    }
    if (!asset) {
      throw new Error("朗读生成失败");
    }

    const tempUrl = await getTempFileUrl(asset.file_id);
    if (sequence !== prepareSequence || state.source_key !== sourceKey) {
      return readMemoirTtsPlaybackState();
    }
    return patchState({
      status: "ready",
      asset,
      temp_url: tempUrl,
      message: "",
      error: "",
    });
  } catch (error) {
    if (sequence !== prepareSequence || state.source_key !== sourceKey) {
      return readMemoirTtsPlaybackState();
    }
    return patchState({
      status: "error",
      message: "",
      error: error instanceof Error ? error.message : "朗读准备失败",
    });
  }
}

export async function playMemoirTtsPlayback(
  source: MemoirTtsPlaybackSource,
  options: Omit<MemoirTtsAssetInput, "source_type" | "source_id"> = {},
): Promise<MemoirTtsPlaybackState> {
  const sourceKey = memoirTtsSourceKey(source);
  const prepared = state.source_key === sourceKey && state.temp_url
    ? state
    : await prepareMemoirTtsPlayback(source, options);
  if (prepared.source_key !== sourceKey) {
    return readMemoirTtsPlaybackState();
  }
  if (prepared.status === "error" || !prepared.temp_url) {
    return prepared;
  }

  const context = ensureAudioContext();
  if (context.src !== prepared.temp_url) {
    context.stop();
    context.src = prepared.temp_url;
  }
  setAudioPlaybackSpeed(state.speed);
  patchState({ status: "playing", message: "", error: "" });
  context.play();
  return readMemoirTtsPlaybackState();
}

export function pauseMemoirTtsPlayback(): MemoirTtsPlaybackState {
  if (audioContext && state.status === "playing") {
    audioContext.pause();
  }
  return patchState({ status: state.temp_url ? "paused" : "idle" });
}

export function stopMemoirTtsPlayback(): MemoirTtsPlaybackState {
  if (audioContext) {
    audioContext.stop();
  }
  return patchState({ status: state.temp_url ? "ready" : "idle", position_seconds: 0 });
}

export function setMemoirTtsPlaybackSpeed(speed: number): MemoirTtsPlaybackState {
  const normalized = normalizeMemoirTtsPlaybackSpeed(speed);
  setAudioPlaybackSpeed(normalized);
  return patchState({ speed: normalized });
}

export function cycleMemoirTtsPlaybackSpeed(): MemoirTtsPlaybackState {
  return setMemoirTtsPlaybackSpeed(nextMemoirTtsPlaybackSpeed(state.speed));
}

export function destroyMemoirTtsPlayback(): void {
  if (audioContext) {
    audioContext.stop();
    audioContext.destroy();
    audioContext = null;
  }
  emit({
    status: "idle",
    source_key: "",
    source_label: "",
    speed: state.speed,
    temp_url: "",
    position_seconds: 0,
    duration_seconds: 0,
    message: "",
    error: "",
  });
}
