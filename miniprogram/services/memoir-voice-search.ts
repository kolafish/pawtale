import { transcribeMemoirVoiceSearchAudio } from "./memoir-voice-api";
import { VoiceActivityDetector } from "./voice-activity-detector";
import { ensureRecorderPrivacyAuthorization, wechatPrivacyErrorMessage } from "./wechat-privacy";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "./voice-authorization";

const VOICE_SEARCH_MAX_DURATION_MS = 10000;
const VOICE_SEARCH_FRAME_SIZE_KB = 4;
const VOICE_SEARCH_SILENCE_LEVEL_THRESHOLD = 0.015;
const VOICE_SEARCH_MIN_VOICE_ACTIVITY_MS = 180;
const VOICE_SEARCH_AUTO_STOP_SILENCE_MS = 900;
const VOICE_SEARCH_NO_VOICE_AUTO_STOP_MS = 3500;
const VOICE_SEARCH_MIN_RECORDING_MS = 700;

interface VoiceSearchRecorderPending {
  resolve(result: WechatMiniprogram.RecorderManagerStopCallbackResult & { noVoiceAutoStopped?: boolean }): void;
  reject(error: Error): void;
  vad: VoiceActivityDetector;
  stopping: boolean;
  noVoiceAutoStopped: boolean;
  stopMode: VoiceInputStopMode;
  onNoVoiceAutoStop?: () => void;
}

type VoiceInputStopMode = "voice_activity" | "manual";

interface RecordMemoirVoiceSearchQueryOptions {
  onRecordingEnd?: () => void;
  onNoVoiceAutoStop?: () => void;
  emptyTranscriptMessage?: string;
  noVoiceTranscriptMessage?: string;
  stopMode?: VoiceInputStopMode;
  maxDurationMs?: number;
}

let recorderManager: WechatMiniprogram.RecorderManager | null = null;
let recorderEventsBound = false;
let pendingRecording: VoiceSearchRecorderPending | null = null;

function getRecorderManager(): WechatMiniprogram.RecorderManager {
  if (!recorderManager) {
    recorderManager = wx.getRecorderManager();
  }
  bindRecorderEvents(recorderManager);
  return recorderManager;
}

function bindRecorderEvents(manager: WechatMiniprogram.RecorderManager): void {
  if (recorderEventsBound) {
    return;
  }
  recorderEventsBound = true;
  manager.onStop((result) => {
    const pending = pendingRecording;
    pendingRecording = null;
    if (!pending) {
      return;
    }
    if (!result.tempFilePath) {
      pending.reject(new Error("没有生成可识别的语音"));
      return;
    }
    pending.resolve({ ...result, noVoiceAutoStopped: pending.noVoiceAutoStopped });
  });
  manager.onError((error) => {
    const pending = pendingRecording;
    pendingRecording = null;
    if (!pending) {
      return;
    }
    pending.reject(new Error(wechatPrivacyErrorMessage(error.errMsg || "录音失败")));
  });
  manager.onFrameRecorded((result) => {
    handleRecordedFrame(result.frameBuffer);
  });
}

function recordVoiceSearchAudio(
  options: Pick<
    RecordMemoirVoiceSearchQueryOptions,
    "onNoVoiceAutoStop" | "stopMode" | "maxDurationMs"
  > = {},
): Promise<WechatMiniprogram.RecorderManagerStopCallbackResult & { noVoiceAutoStopped?: boolean }> {
  if (pendingRecording) {
    return Promise.reject(new Error("已有语音输入正在录音"));
  }
  const manager = getRecorderManager();
  return new Promise((resolve, reject) => {
    pendingRecording = {
      resolve,
      reject,
      vad: new VoiceActivityDetector({
        silenceLevelThreshold: VOICE_SEARCH_SILENCE_LEVEL_THRESHOLD,
        minVoiceActivityMs: VOICE_SEARCH_MIN_VOICE_ACTIVITY_MS,
        trackPreVoiceSilence: true,
      }),
      stopping: false,
      noVoiceAutoStopped: false,
      stopMode: options.stopMode ?? "voice_activity",
      onNoVoiceAutoStop: options.onNoVoiceAutoStop,
    };
    try {
      manager.start({
        duration: options.maxDurationMs ?? VOICE_SEARCH_MAX_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
        frameSize: VOICE_SEARCH_FRAME_SIZE_KB,
        format: "PCM",
      });
    } catch (error) {
      pendingRecording = null;
      reject(error instanceof Error ? error : new Error("录音启动失败"));
    }
  });
}

function readFileAsBase64(filePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().readFile({
      filePath,
      encoding: "base64",
      success: (result) => {
        if (typeof result.data === "string" && result.data.trim()) {
          resolve(result.data);
          return;
        }
        reject(new Error("语音读取失败"));
      },
      fail: (error) => reject(new Error(error.errMsg || "语音读取失败")),
    });
  });
}

function handleRecordedFrame(frameBuffer: ArrayBuffer): void {
  const pending = pendingRecording;
  if (!pending || pending.stopping || !frameBuffer?.byteLength) {
    return;
  }
  const vad = pending.vad.update(frameBuffer);
  if (pending.stopMode === "manual") {
    return;
  }
  if (
    !vad.hasVoiceActivity
    && vad.silentElapsedMs >= VOICE_SEARCH_NO_VOICE_AUTO_STOP_MS
  ) {
    pending.noVoiceAutoStopped = true;
    pending.onNoVoiceAutoStop?.();
    stopMemoirVoiceSearchRecording();
    return;
  }
  if (!vad.hasVoiceActivity) {
    return;
  }
  if (
    vad.recordingElapsedMs >= VOICE_SEARCH_MIN_RECORDING_MS
    && vad.silentElapsedMs >= VOICE_SEARCH_AUTO_STOP_SILENCE_MS
  ) {
    stopMemoirVoiceSearchRecording();
  }
}

export function stopMemoirVoiceSearchRecording(): void {
  const pending = pendingRecording;
  if (!pending || pending.stopping) {
    return;
  }
  pending.stopping = true;
  try {
    getRecorderManager().stop();
  } catch (error) {
    pendingRecording = null;
    pending.reject(error instanceof Error ? error : new Error("录音停止失败"));
  }
}

export async function recordMemoirVoiceSearchQuery(
  options: RecordMemoirVoiceSearchQueryOptions = {},
): Promise<string> {
  if (!readVoiceAuthorizationConsent()) {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
    throw new Error("请先同意声纹授权协议");
  }
  await syncVoiceAuthorizationConsent();
  await ensureRecorderPrivacyAuthorization();
  let noVoiceAutoStopped = false;
  const audio = await recordVoiceSearchAudio({
    stopMode: options.stopMode,
    maxDurationMs: options.maxDurationMs,
    onNoVoiceAutoStop: () => {
      noVoiceAutoStopped = true;
      options.onNoVoiceAutoStop?.();
    },
  });
  options.onRecordingEnd?.();
  const audioBase64 = await readFileAsBase64(audio.tempFilePath);
  const response = await transcribeMemoirVoiceSearchAudio(audioBase64, "audio/pcm");
  const transcript = response.transcript.trim();
  if (!transcript) {
    const wasNoVoice = noVoiceAutoStopped || Boolean(audio.noVoiceAutoStopped);
    throw new Error(
      wasNoVoice
        ? options.noVoiceTranscriptMessage || options.emptyTranscriptMessage || "没有听到声音，请再说一次"
        : options.emptyTranscriptMessage || "没有识别到语音内容，请换个说法再试",
    );
  }
  return transcript;
}
