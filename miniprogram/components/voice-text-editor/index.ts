import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import {
  AUTO_LISTEN_NO_ANSWER_MS,
  AUTO_STOP_SILENCE_MS,
  AUTO_STOP_MIN_RECORDING_MS,
} from "../../services/voice-activity-detector";
import {
  ensureVoiceRecordingAuthorization,
} from "../../services/voice-authorization";

type VoicePhase = "idle" | "connecting" | "listening" | "stopping";
const VOICE_MAX_DURATION_MS = 30000;

interface VoiceRuntime {
  streaming: StreamingVoiceController;
  token: number;
  text: string;
  baseText: string;
  transcript: string;
  hasFinalTranscript: boolean;
  attached: boolean;
  autoStartAttempted: boolean;
}

const voiceRuntimes = new WeakMap<object, VoiceRuntime>();

function voiceRuntime(owner: object): VoiceRuntime {
  let runtime = voiceRuntimes.get(owner);
  if (!runtime) {
    runtime = {
      streaming: new StreamingVoiceController(),
      token: 0,
      text: "",
      baseText: "",
      transcript: "",
      hasFinalTranscript: false,
      attached: false,
      autoStartAttempted: false,
    };
    voiceRuntimes.set(owner, runtime);
  }
  return runtime;
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
    autoStart: { type: Boolean, value: false },
    submitOnVoiceFinal: { type: Boolean, value: false },
  },

  data: {
    textReady: false,
    voicePhase: "idle" as VoicePhase,
    error: "",
  },

  observers: {
    value(value: unknown) {
      this.syncText(value);
    },
    "autoStart,gatewayUrl,disabled,busy"() {
      this.tryAutoStart();
    },
  },

  lifetimes: {
    attached() {
      voiceRuntime(this).attached = true;
      this.syncText(this.properties.value);
      this.tryAutoStart();
    },
    detached() {
      voiceRuntime(this).attached = false;
      this.cancelVoice();
      voiceRuntimes.delete(this);
    },
  },

  pageLifetimes: {
    show() {
      this.tryAutoStart();
    },
    hide() {
      this.cancelVoice();
    },
  },

  methods: {
    syncText(value: unknown) {
      if (this.data.voicePhase === "idle") {
        const text = String(value || "");
        voiceRuntime(this).text = text;
        this.setData({ textReady: Boolean(text.trim()) });
      }
    },

    canEdit() {
      return !this.properties.disabled
        && !this.properties.busy
        && this.data.voicePhase === "idle";
    },

    changeText(event: { detail: { value?: string } }) {
      if (this.canEdit()) {
        this.updateText(String(event.detail.value || ""));
      }
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
      runtime.streaming.close({ force: true });
      runtime.baseText = "";
      runtime.transcript = "";
      runtime.hasFinalTranscript = false;
      this.setData({ voicePhase: "idle" });
    },

    tryAutoStart() {
      const runtime = voiceRuntime(this);
      if (
        !runtime.attached
        || runtime.autoStartAttempted
        || !this.properties.autoStart
        || !this.properties.gatewayUrl
        || !this.canEdit()
      ) {
        return;
      }
      runtime.autoStartAttempted = true;
      void this.startVoice(true);
    },

    async toggleVoice() {
      const runtime = voiceRuntime(this);
      if (this.data.voicePhase === "listening") {
        runtime.streaming.stopRecording();
        return;
      }
      await this.startVoice(false);
    },

    async startVoice(autoTriggered: boolean = false) {
      const runtime = voiceRuntime(this);
      if (!this.canEdit()) {
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
        if (!(await ensureVoiceRecordingAuthorization())) {
          if (autoTriggered) {
            runtime.autoStartAttempted = false;
          }
          this.setData({ voicePhase: "idle" });
          return;
        }
        if (token !== runtime.token) {
          return;
        }
        runtime.baseText = runtime.text;
        runtime.transcript = "";
        runtime.hasFinalTranscript = false;
        await runtime.streaming.connect({
          voiceSessionId: this.properties.voiceSessionId || `dictation-${token}`,
          gatewayUrl: this.properties.gatewayUrl,
          guidanceMode: "free_narration",
          autoStopSilenceMs: AUTO_STOP_SILENCE_MS,
          minAutoStopRecordingMs: AUTO_STOP_MIN_RECORDING_MS,
          noVoiceActivityTimeoutMs: 0,
          callbacks: {
            onStatus: (status) => {
              if (token !== runtime.token) {
                return;
              }
              if (status === "closed" || status === "error") {
                this.setData({ voicePhase: "idle" });
              } else if (status === "listening" || status === "stopping") {
                this.setData({ voicePhase: status });
              }
            },
            onEvent: (event) => {
              if (token === runtime.token && event.type === "audio.ended") {
                void this.finishVoice(token);
              }
            },
            onPartial: (text) => this.updateVoiceTranscript(token, text, false),
            onFinal: (event) => this.updateVoiceTranscript(token, event.text || "", true),
            onError: (message) => {
              if (token === runtime.token) {
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
        const recordingOptions = autoTriggered
          ? {
              maxDurationMs: VOICE_MAX_DURATION_MS,
              noVoiceActivityTimeoutMs: AUTO_LISTEN_NO_ANSWER_MS,
            }
          : { maxDurationMs: VOICE_MAX_DURATION_MS };
        if (!runtime.streaming.startRecording(recordingOptions)) {
          throw new Error("录音启动失败，请重试");
        }
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

    updateVoiceTranscript(token: number, transcript: string, finalized: boolean) {
      const runtime = voiceRuntime(this);
      if (token !== runtime.token) {
        return;
      }
      runtime.transcript = transcript.trim();
      if (finalized) {
        runtime.hasFinalTranscript = Boolean(runtime.transcript);
      }
      const separator = runtime.baseText.trim() && runtime.transcript ? "\n" : "";
      this.updateText(
        `${runtime.baseText.trim()}${separator}${runtime.transcript}`.slice(0, this.properties.maxlength),
      );
    },

    async finishVoice(token: number) {
      const runtime = voiceRuntime(this);
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
      const shouldSubmit = Boolean(
        transcript
        && runtime.hasFinalTranscript
        && this.properties.submitOnVoiceFinal,
      );
      runtime.baseText = "";
      runtime.transcript = "";
      runtime.hasFinalTranscript = false;
      this.setData({
        voicePhase: "idle",
        ...(transcript ? {} : { error: "没有识别到内容，请再说一次" }),
      });
      if (transcript) {
        wx.showToast({
          title: shouldSubmit ? "已听完，正在理解" : "语音已转成文字",
          icon: "none",
        });
      }
      if (shouldSubmit) {
        this.submit();
      }
    },

    submit() {
      const value = voiceRuntime(this).text.trim();
      if (!value || !this.canEdit()) {
        if (!value) {
          wx.showToast({ title: "请先输入或说出内容", icon: "none" });
        }
        return;
      }
      this.triggerEvent("submit", { value });
    },

    cancel() {
      if (this.canEdit()) {
        this.triggerEvent("cancel");
      }
    },
  },
});
