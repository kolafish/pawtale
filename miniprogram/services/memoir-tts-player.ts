import type { MemoirTtsAsset, MemoirTtsSourceType } from "../domain/memoir";
import {
  createMemoirTtsAsset,
  processMemoirTtsJob,
  type MemoirTtsAssetInput,
} from "./memoir";
import {
  MEMOIR_TTS_PLAYBACK_SPEEDS,
  normalizeMemoirTtsPlaybackSpeed,
  normalizeMemoirTtsVoice,
  readMemoirTtsSettings,
  writeMemoirTtsSettings,
} from "./memoir-tts-settings";

export { MEMOIR_TTS_PLAYBACK_SPEEDS };

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
  voice: string;
  speed: number;
  asset?: MemoirTtsAsset;
  temp_url: string;
  position_seconds: number;
  duration_seconds: number;
  message: string;
  error: string;
}

type MemoirTtsPlaybackListener = (state: MemoirTtsPlaybackState) => void;

interface PreparedMemoirTtsPlayback {
  source_key: string;
  source_label: string;
  voice: string;
  asset: MemoirTtsAsset;
  temp_url: string;
}

let audioContext: WechatMiniprogram.InnerAudioContext | null = null;
const listeners = new Set<MemoirTtsPlaybackListener>();
const preparedCache = new Map<string, PreparedMemoirTtsPlayback>();
const preparedInFlight = new Map<string, Promise<PreparedMemoirTtsPlayback>>();
let preparedCacheRevision = 0;
let prepareSequence = 0;
const initialSettings = readMemoirTtsSettings();

let state: MemoirTtsPlaybackState = {
  status: "idle",
  source_key: "",
  source_label: "",
  voice: initialSettings.voice,
  speed: initialSettings.speed,
  temp_url: "",
  position_seconds: 0,
  duration_seconds: 0,
  message: "",
  error: "",
};

export function memoirTtsSourceKey(source: MemoirTtsPlaybackSource): string {
  return `${source.source_type}:${source.source_id}`;
}
export { normalizeMemoirTtsPlaybackSpeed, normalizeMemoirTtsVoice };

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
  wx.setInnerAudioOption?.({ obeyMuteSwitch: false, mixWithOther: true });
  audioContext = wx.createInnerAudioContext();
  audioContext.obeyMuteSwitch = false;
  audioContext.volume = 1;
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

function playbackOptions(options: Omit<MemoirTtsAssetInput, "source_type" | "source_id">): Omit<MemoirTtsAssetInput, "source_type" | "source_id"> {
  return {
    voice: options.voice || readMemoirTtsSettings().voice,
    model: options.model,
    format: options.format,
    synthesis_speed: options.synthesis_speed,
  };
}

function preparedCacheKey(sourceKey: string, voice: string): string {
  return `${sourceKey}:${voice}`;
}

async function loadPreparedMemoirTtsPlayback(
  source: MemoirTtsPlaybackSource,
  options: Omit<MemoirTtsAssetInput, "source_type" | "source_id">,
  onGenerating?: () => void,
): Promise<PreparedMemoirTtsPlayback> {
  const sourceKey = memoirTtsSourceKey(source);
  const voice = normalizeMemoirTtsVoice(options.voice);
  const cacheKey = preparedCacheKey(sourceKey, voice);
  const cached = preparedCache.get(cacheKey);
  if (cached) {
    return cached;
  }
  const inFlight = preparedInFlight.get(cacheKey);
  if (inFlight) {
    return inFlight;
  }

  const cacheRevision = preparedCacheRevision;
  const preparedPromise = (async () => {
    const created = await createMemoirTtsAsset({
      source_type: source.source_type,
      source_id: source.source_id,
      ...options,
    });
    let asset = created.asset || null;
    if (!asset && created.job) {
      onGenerating?.();
      const job = await processMemoirTtsJob(created.job.id);
      if (job.status !== "completed" || !job.asset) {
        throw new Error(job.error_message || "朗读生成失败");
      }
      asset = job.asset;
    }
    if (!asset) {
      throw new Error("朗读生成失败");
    }

    const prepared = {
      source_key: sourceKey,
      source_label: source.label || "",
      voice,
      asset,
      temp_url: await getTempFileUrl(asset.file_id),
    };
    if (cacheRevision === preparedCacheRevision) {
      preparedCache.set(cacheKey, prepared);
    }
    return prepared;
  })();

  preparedInFlight.set(cacheKey, preparedPromise);
  try {
    return await preparedPromise;
  } finally {
    preparedInFlight.delete(cacheKey);
  }
}

export async function preloadMemoirTtsPlayback(
  source: MemoirTtsPlaybackSource,
  options: Omit<MemoirTtsAssetInput, "source_type" | "source_id"> = {},
): Promise<void> {
  try {
    await loadPreparedMemoirTtsPlayback(source, playbackOptions(options));
  } catch {
    // Preload is opportunistic. User-triggered playback still reports the real error.
  }
}

export function invalidateMemoirTtsPlayback(source: MemoirTtsPlaybackSource): MemoirTtsPlaybackState {
  const sourceKey = memoirTtsSourceKey(source);
  preparedCacheRevision += 1;
  for (const cacheKey of preparedCache.keys()) {
    if (cacheKey.startsWith(`${sourceKey}:`)) {
      preparedCache.delete(cacheKey);
    }
  }
  for (const cacheKey of preparedInFlight.keys()) {
    if (cacheKey.startsWith(`${sourceKey}:`)) {
      preparedInFlight.delete(cacheKey);
    }
  }
  if (state.source_key !== sourceKey) {
    return readMemoirTtsPlaybackState();
  }

  prepareSequence += 1;
  if (audioContext) {
    audioContext.stop();
  }
  return patchState({
    status: "idle",
    source_key: "",
    source_label: "",
    asset: undefined,
    temp_url: "",
    position_seconds: 0,
    duration_seconds: 0,
    message: "",
    error: "",
  });
}

export async function prepareMemoirTtsPlayback(
  source: MemoirTtsPlaybackSource,
  options: Omit<MemoirTtsAssetInput, "source_type" | "source_id"> = {},
): Promise<MemoirTtsPlaybackState> {
  const sourceKey = memoirTtsSourceKey(source);
  const resolvedOptions = playbackOptions(options);
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
    voice: normalizeMemoirTtsVoice(resolvedOptions.voice),
    message: "",
    error: "",
  });

  try {
    const prepared = await loadPreparedMemoirTtsPlayback(source, resolvedOptions, () => {
      if (sequence === prepareSequence && state.source_key === sourceKey) {
        patchState({ status: "generating", message: "" });
      }
    });
    if (sequence !== prepareSequence || state.source_key !== sourceKey) {
      return readMemoirTtsPlaybackState();
    }
    return patchState({
      status: "ready",
      asset: prepared.asset,
      temp_url: prepared.temp_url,
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
  const resolvedOptions = playbackOptions(options);
  const voice = normalizeMemoirTtsVoice(resolvedOptions.voice);
  const prepared = state.source_key === sourceKey && state.temp_url && state.voice === voice
    ? state
    : await prepareMemoirTtsPlayback(source, resolvedOptions);
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
  writeMemoirTtsSettings({ speed: normalized });
  setAudioPlaybackSpeed(normalized);
  return patchState({ speed: normalized });
}

export function setMemoirTtsPlaybackVoice(voice: string): MemoirTtsPlaybackState {
  const normalized = normalizeMemoirTtsVoice(voice);
  writeMemoirTtsSettings({ voice: normalized });
  if (state.voice === normalized) {
    return readMemoirTtsPlaybackState();
  }
  preparedCache.clear();
  preparedInFlight.clear();
  preparedCacheRevision += 1;
  prepareSequence += 1;
  if (audioContext) {
    audioContext.stop();
  }
  return patchState({
    status: "idle",
    source_key: "",
    source_label: "",
    voice: normalized,
    asset: undefined,
    temp_url: "",
    position_seconds: 0,
    duration_seconds: 0,
    message: "",
    error: "",
  });
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
    voice: state.voice,
    speed: state.speed,
    temp_url: "",
    position_seconds: 0,
    duration_seconds: 0,
    message: "",
    error: "",
  });
}
