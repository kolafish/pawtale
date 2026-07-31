import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";

type VoicePhase = "idle" | "connecting" | "listening" | "stopping";
const VOICE_MAX_DURATION_MS = 30000;

interface VoiceRuntime {
  streaming: StreamingVoiceController;
  timer: ReturnType<typeof setTimeout> | null;
  token: number;
  text: string;
  baseText: string;
  transcript: string;
}

const voiceRuntimes = new WeakMap<object, VoiceRuntime>();

function voiceRuntime(owner: object): VoiceRuntime {
  let runtime = voiceRuntimes.get(owner);
  if (!runtime) {
    runtime = {
      streaming: new StreamingVoiceController(),
      timer: null,
      token: 0,
      text: "",
      baseText: "",
      transcript: "",
    };
    voiceRuntimes.set(owner, runtime);
  }
  return runtime;
}

export function voiceTextWithTranscript(
  existing: string,
  transcript: string,
  maxLength: number,
): string {
  const base = existing.trim();
  const live = transcript.trim();
  return `${base}${base && live ? "\n" : ""}${live}`.slice(0, maxLength);
}

Component({
  properties: {
    value: { type: String, value: "" },
    gatewayUrl: { type: String, value: "" },
    voiceSessionId: { type: String, value: "" },
    placeholder: { type: String, value: "请输入或说出您的意见" },
    submitText: { type: String, value: "确认" },
    busyText: { type: String, value: "正在处理…" },
    cancelText: { type: String, value: "" },
    hint: { type: String, value: "" },
    ariaLabel: { type: String, value: "文字或语音输入" },
    maxlength: { type: Number, value: 1000 },
    disabled: { type: Boolean, value: false },
    busy: { type: Boolean, value: false },
  },

  data: {
    textReady: false,
    voicePhase: "idle" as VoicePhase,
    error: "",
  },

  observers: {
    value(value: unknown) {
      if (this.data.voicePhase !== "idle") {
        return;
      }
      const text = String(value || "");
      voiceRuntime(this).text = text;
      this.setData({ textReady: Boolean(text.trim()) });
    },
  },

  lifetimes: {
    attached() {
      const text = String(this.properties.value || "");
      voiceRuntime(this).text = text;
      this.setData({ textReady: Boolean(text.trim()) });
    },
    detached() {
      this.cancelVoice();
      voiceRuntimes.delete(this);
    },
  },

  pageLifetimes: {
    hide() {
      this.cancelVoice();
    },
  },

  methods: {
    changeText(event: { detail: { value?: string } }) {
      if (
        this.properties.disabled
        || this.properties.busy
        || this.data.voicePhase !== "idle"
      ) {
        return;
      }
      this.updateText(String(event.detail.value || ""));
    },

    updateText(text: string) {
      voiceRuntime(this).text = text;
      this.setData({
        textReady: Boolean(text.trim()),
        error: "",
      });
      this.triggerEvent("change", { value: text });
    },

    cancelVoice() {
      const runtime = voiceRuntime(this);
      runtime.token += 1;
      this.clearVoiceTimer();
      runtime.streaming.close({ force: true });
      runtime.baseText = "";
      runtime.transcript = "";
      this.setData({ voicePhase: "idle" });
    },

    async toggleVoice() {
      const runtime = voiceRuntime(this);
      if (this.data.voicePhase === "listening") {
        this.clearVoiceTimer();
        runtime.streaming.stopRecording();
        return;
      }
      if (
        this.properties.disabled
        || this.properties.busy
        || this.data.voicePhase !== "idle"
      ) {
        return;
      }
      if (!this.properties.gatewayUrl) {
        this.setData({ error: "语音输入暂时不可用，请使用文字输入" });
        return;
      }
      runtime.token += 1;
      const token = runtime.token;
      this.setData({ voicePhase: "connecting", error: "" });
      try {
        if (!readVoiceAuthorizationConsent()) {
          wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
          this.setData({ voicePhase: "idle" });
          return;
        }
        await syncVoiceAuthorizationConsent();
        await ensureRecorderPrivacyAuthorization();
        if (token !== runtime.token) {
          return;
        }
        runtime.baseText = runtime.text;
        runtime.transcript = "";
        await runtime.streaming.connect({
          voiceSessionId: this.properties.voiceSessionId || `dictation-${token}`,
          gatewayUrl: this.properties.gatewayUrl,
          guidanceMode: "free_narration",
          autoStopSilenceMs: 0,
          noVoiceActivityTimeoutMs: 0,
          callbacks: {
            onStatus: (status) => {
              if (token !== runtime.token) {
                return;
              }
              if (status === "listening") {
                this.setData({ voicePhase: "listening" });
              } else if (status === "stopping") {
                this.setData({ voicePhase: "stopping" });
              } else if (status === "closed" || status === "error") {
                this.clearVoiceTimer();
                this.setData({ voicePhase: "idle" });
              }
            },
            onEvent: (event) => {
              if (token === runtime.token && event.type === "audio.ended") {
                void this.finishVoice(token);
              }
            },
            onPartial: (text) => this.updateVoiceTranscript(token, text),
            onFinal: (event) => (
              this.updateVoiceTranscript(token, event.text || "")
            ),
            onError: (message) => {
              if (token === runtime.token) {
                this.clearVoiceTimer();
                this.setData({
                  voicePhase: "idle",
                  error: message || "语音输入失败",
                });
              }
            },
          },
        });
        if (token !== runtime.token) {
          runtime.streaming.close({ force: true });
          return;
        }
        if (!runtime.streaming.startRecording()) {
          throw new Error("录音启动失败，请重试");
        }
        runtime.timer = setTimeout(() => {
          if (token === runtime.token) {
            runtime.timer = null;
            runtime.streaming.stopRecording();
          }
        }, VOICE_MAX_DURATION_MS);
      } catch (error) {
        if (token !== runtime.token) {
          return;
        }
        runtime.streaming.close({ force: true });
        this.setData({
          voicePhase: "idle",
          error: error instanceof Error ? error.message : "语音输入失败",
        });
      }
    },

    updateVoiceTranscript(token: number, transcript: string) {
      const runtime = voiceRuntime(this);
      if (token !== runtime.token) {
        return;
      }
      runtime.transcript = transcript.trim();
      this.updateText(voiceTextWithTranscript(
        runtime.baseText,
        runtime.transcript,
        this.properties.maxlength,
      ));
    },

    clearVoiceTimer() {
      const runtime = voiceRuntime(this);
      if (runtime.timer) {
        clearTimeout(runtime.timer);
        runtime.timer = null;
      }
    },

    async finishVoice(token: number) {
      const runtime = voiceRuntime(this);
      this.clearVoiceTimer();
      try {
        await runtime.streaming.finishSession();
      } catch (error) {
        if (token === runtime.token) {
          this.setData({
            error: error instanceof Error
              ? error.message
              : "语音输入结束失败",
          });
        }
      }
      if (token !== runtime.token) {
        return;
      }
      const transcript = runtime.transcript.trim();
      runtime.baseText = "";
      runtime.transcript = "";
      this.setData({
        voicePhase: "idle",
        ...(transcript ? {} : { error: "没有识别到内容，请再说一次" }),
      });
      if (transcript) {
        wx.showToast({ title: "语音已转成文字", icon: "none" });
      }
    },

    submit() {
      const value = voiceRuntime(this).text.trim();
      if (
        !value
        || this.properties.disabled
        || this.properties.busy
        || this.data.voicePhase !== "idle"
      ) {
        if (!value) {
          wx.showToast({ title: "请先输入或说出内容", icon: "none" });
        }
        return;
      }
      this.triggerEvent("submit", { value });
    },

    cancel() {
      if (
        this.properties.disabled
        || this.properties.busy
        || this.data.voicePhase !== "idle"
      ) {
        return;
      }
      this.triggerEvent("cancel");
    },
  },
});
