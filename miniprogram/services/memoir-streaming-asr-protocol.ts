import type {
  VoiceChatErrorInfo,
  VoiceChatErrorKind,
  VoiceGatewayServerEvent,
} from "../domain/voice-chat";

const PCM_SAMPLE_RATE = 16000;
const PCM_BYTES_PER_SAMPLE = 2;

export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const bytes = new Uint8Array(buffer);
  let output = "";
  let index = 0;
  while (index < bytes.length) {
    const first = bytes[index++];
    const second = index < bytes.length ? bytes[index++] : Number.NaN;
    const third = index < bytes.length ? bytes[index++] : Number.NaN;
    output += chars[first >> 2];
    output += chars[((first & 0x03) << 4) | ((second || 0) >> 4)];
    output += Number.isNaN(second) ? "=" : chars[((second & 0x0F) << 2) | ((third || 0) >> 6)];
    output += Number.isNaN(third) ? "=" : chars[third & 0x3F];
  }
  return output;
}

export function normalizeVoiceGatewayEvent(raw: unknown): VoiceGatewayServerEvent {
  if (!raw || typeof raw !== "object") {
    return { type: "session.error", error: "invalid_gateway_event" };
  }
  const event = raw as VoiceGatewayServerEvent;
  return {
    ...event,
    type: typeof event.type === "string" ? event.type : "session.error",
  };
}

export function estimatePcm16Level(buffer: ArrayBuffer): number {
  if (buffer.byteLength < PCM_BYTES_PER_SAMPLE) {
    return 0;
  }
  const view = new DataView(buffer);
  const sampleCount = Math.floor(buffer.byteLength / PCM_BYTES_PER_SAMPLE);
  let squareSum = 0;
  for (let index = 0; index < sampleCount; index += 1) {
    const sample = view.getInt16(index * PCM_BYTES_PER_SAMPLE, true);
    squareSum += sample * sample;
  }
  return Math.min(1, Math.sqrt(squareSum / sampleCount) / 32768);
}

export function pcmFrameDurationMs(buffer: ArrayBuffer): number {
  if (buffer.byteLength <= 0) {
    return 0;
  }
  return Math.max(1, Math.round((buffer.byteLength / PCM_BYTES_PER_SAMPLE / PCM_SAMPLE_RATE) * 1000));
}

export function classifyVoiceGatewayError(event: VoiceGatewayServerEvent): VoiceChatErrorInfo {
  const code = normalizeVoiceGatewayErrorCode(event.error || "voice_gateway_error");
  const kind = errorKindFromCode(code);
  return {
    kind,
    code,
    state: event.state,
    message: messageFromErrorCode(code, kind),
  };
}

function normalizeVoiceGatewayErrorCode(code: string): string {
  const normalized = code.toLowerCase();
  if (normalized.startsWith("volc asr frame")) {
    return "asr_provider_invalid_frame";
  }
  return code;
}

export function classifyVoiceClientConnectionError(message: string): VoiceChatErrorInfo {
  const rawMessage = message || "语音连接失败";
  let code = "voice_gateway_connection_failed";
  if (rawMessage.includes("HTTP 403") || rawMessage.includes("rejected WebSocket")) {
    code = "voice_gateway_connection_forbidden";
  } else if (rawMessage.includes("超时") || rawMessage.toLowerCase().includes("timeout")) {
    code = "voice_gateway_connection_timeout";
  } else if (rawMessage.includes("关闭") || rawMessage.toLowerCase().includes("closed")) {
    code = "voice_gateway_connection_closed";
  }
  const kind = errorKindFromCode(code);
  return {
    kind,
    code,
    message: messageFromErrorCode(code, kind),
  };
}

function errorKindFromCode(code: string): VoiceChatErrorKind {
  const normalized = code.toLowerCase();
  if (normalized.startsWith("voice_gateway_connection") || normalized === "voice_gateway_auth_failed") {
    return "connection";
  }
  if (normalized.startsWith("voice_session_")) {
    return "gateway";
  }
  if (normalized.includes("persistence") || normalized.includes("sqlite") || normalized.includes("database")) {
    return "persistence";
  }
  if (normalized.includes("timeout") || normalized.includes("超时")) {
    return "timeout";
  }
  if (
    normalized.startsWith("audio_frame")
    || normalized.startsWith("audio_stream")
    || normalized.startsWith("unsupported_gateway")
    || normalized.startsWith("asr_session")
  ) {
    return "gateway";
  }
  if (
    normalized.startsWith("asr_provider")
    || normalized.startsWith("provider_")
    || normalized.startsWith("volc")
    || normalized.includes("asr_")
  ) {
    return "asr";
  }
  return "unknown";
}

function messageFromErrorCode(code: string, kind: VoiceChatErrorKind): string {
  const messages: Partial<Record<string, string>> = {
    asr_final_persistence_failed: "转写已返回，但保存识别结果失败",
    asr_finish_timeout: "语音识别收尾超时",
    audio_frame_invalid_base64: "音频帧格式异常",
    audio_frame_missing_base64: "音频帧缺少数据",
    audio_frame_too_large: "单次音频数据过大",
    audio_stream_too_large: "本次录音超过可保存大小",
    asr_session_not_started: "语音识别还没有开始",
    asr_session_finishing: "上一句语音还在收尾",
    asr_provider_connection_failed: "语音识别服务连接失败",
    asr_provider_connection_timeout: "语音识别服务连接超时",
    asr_provider_forbidden: "语音识别服务鉴权失败",
    asr_provider_invalid_frame: "语音识别服务返回异常，请重试",
    asr_provider_invalid_credentials: "语音识别 API Key 无效",
    asr_provider_resource_not_granted: "ASR 资源未开通或 API Key 无权限",
    voice_gateway_auth_failed: "登录状态已失效，请重新登录后再进入",
    voice_gateway_connection_closed: "语音连接已关闭，请重新连接",
    voice_gateway_connection_failed: "语音连接失败，请重新连接",
    voice_gateway_connection_forbidden: "语音连接被拒绝，请返回后重新进入",
    voice_gateway_connection_timeout: "语音连接超时，请检查网络后重试",
    voice_session_unavailable: "语音会话不存在或已失效，请重新开始",
    provider_empty_payload: "语音识别服务返回了空结果",
    unsupported_gateway_event: "语音连接事件不支持",
  };
  if (messages[code]) {
    return messages[code];
  }
  if (kind === "persistence") {
    return "识别结果保存失败";
  }
  if (kind === "asr") {
    return "语音识别服务异常";
  }
  if (kind === "gateway") {
    return "语音连接状态异常";
  }
  if (kind === "timeout") {
    return "语音处理超时";
  }
  return code || "语音处理失败";
}

export function clientError(message: string, kind: VoiceChatErrorKind, code?: string): VoiceChatErrorInfo {
  return {
    kind,
    code,
    message,
  };
}
