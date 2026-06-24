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
const VOICE_SEARCH_MIN_RECORDING_MS = 700;

interface VoiceSearchRecorderPending {
  resolve(result: WechatMiniprogram.RecorderManagerStopCallbackResult): void;
  reject(error: Error): void;
  vad: VoiceActivityDetector;
  stopping: boolean;
}

interface RecordMemoirVoiceSearchQueryOptions {
  onRecordingEnd?: () => void;
  emptyTranscriptMessage?: string;
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
      pending.reject(new Error("没有生成可识别的搜索语音"));
      return;
    }
    pending.resolve(result);
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

function recordVoiceSearchAudio(): Promise<WechatMiniprogram.RecorderManagerStopCallbackResult> {
  if (pendingRecording) {
    return Promise.reject(new Error("已有语音搜索正在录音"));
  }
  const manager = getRecorderManager();
  return new Promise((resolve, reject) => {
    pendingRecording = {
      resolve,
      reject,
      vad: new VoiceActivityDetector({
        silenceLevelThreshold: VOICE_SEARCH_SILENCE_LEVEL_THRESHOLD,
        minVoiceActivityMs: VOICE_SEARCH_MIN_VOICE_ACTIVITY_MS,
      }),
      stopping: false,
    };
    try {
      manager.start({
        duration: VOICE_SEARCH_MAX_DURATION_MS,
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
        reject(new Error("搜索语音读取失败"));
      },
      fail: (error) => reject(new Error(error.errMsg || "搜索语音读取失败")),
    });
  });
}

function handleRecordedFrame(frameBuffer: ArrayBuffer): void {
  const pending = pendingRecording;
  if (!pending || pending.stopping || !frameBuffer?.byteLength) {
    return;
  }
  const vad = pending.vad.update(frameBuffer);
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
  const audio = await recordVoiceSearchAudio();
  options.onRecordingEnd?.();
  const audioBase64 = await readFileAsBase64(audio.tempFilePath);
  const response = await transcribeMemoirVoiceSearchAudio(audioBase64, "audio/pcm");
  const transcript = response.transcript.trim();
  if (!transcript) {
    throw new Error(options.emptyTranscriptMessage || "没有识别到搜索内容，请换个说法再试");
  }
  return transcript;
}
