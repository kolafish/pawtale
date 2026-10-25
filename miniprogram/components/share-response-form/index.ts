import { submitShareResponse, startShareResponseVoice } from "../../services/share-responses";
import { ApiError } from "../../services/request";

const runtimes = new WeakMap<object, { generation: number; voiceGeneration: number; submitting: boolean;
  targetReadingToken: string | null; targetVideoCode: string | null; requestId: string; requestContent: string }>();
function runtime(owner: object) {
  let value = runtimes.get(owner);
  if (!value) { value = { generation: 0, voiceGeneration: 0, submitting: false,
    targetReadingToken: null, targetVideoCode: null, requestId: "", requestContent: "" }; runtimes.set(owner, value); }
  return value;
}

Component({
  options: { addGlobalClass: true },
  properties: { readingToken: { type: String, value: "" }, videoCode: { type: String, value: "" }, active: { type: Boolean, value: true } },
  data: { open: false, content: "", busy: false, error: "", sent: false,
    voiceCaptureId: "", gatewayUrl: "", voiceStartRequest: 0 },
  observers: {
    active(active: unknown) {
      if (active) return;
      const current = runtime(this);
      current.voiceGeneration += 1;
      this.setData({ busy: current.submitting, gatewayUrl: "", voiceStartRequest: 0 });
    },
    "readingToken,videoCode"() {
      const current = runtime(this);
      if (current.targetReadingToken === this.properties.readingToken && current.targetVideoCode === this.properties.videoCode) return;
      current.targetReadingToken = this.properties.readingToken;
      current.targetVideoCode = this.properties.videoCode;
      current.generation += 1;
      current.submitting = false;
      current.requestId = "";
      current.requestContent = "";
      this.setData({ open: false, content: "", busy: false, error: "", sent: false,
        voiceCaptureId: "", gatewayUrl: "", voiceStartRequest: 0 });
    },
  },
  lifetimes: { detached() { runtime(this).generation += 1; } },
  methods: {
    openForm() { if (this.properties.active) this.setData({ open: true, sent: false }); },
    closeForm() { if (!this.data.busy) this.setData({ open: false, gatewayUrl: "", voiceStartRequest: 0 }); },
    changeText(event: { detail: { value?: string } }) { this.setData({ content: event.detail.value || "", error: "" }); },
    async requestVoice() {
      if (!this.properties.active || this.data.busy || this.data.voiceCaptureId) return;
      runtime(this).targetReadingToken = this.properties.readingToken;
      runtime(this).targetVideoCode = this.properties.videoCode;
      const generation = runtime(this).generation;
      const voiceGeneration = runtime(this).voiceGeneration;
      const isCurrent = () => generation === runtime(this).generation && voiceGeneration === runtime(this).voiceGeneration && this.properties.active;
      this.setData({ busy: true, error: "" });
      try {
        const target = this.properties.readingToken ? { reading_token: this.properties.readingToken } : { video_code: this.properties.videoCode };
        const ticket = await startShareResponseVoice(target);
        if (!isCurrent()) return;
        this.setData({ gatewayUrl: ticket.gatewayUrl, busy: false }, () => {
          if (isCurrent() && this.data.open) this.setData({ voiceStartRequest: this.data.voiceStartRequest + 1 });
        });
      } catch (error) {
        if (!isCurrent()) return;
        if (error instanceof ApiError && error.statusCode === 401) {
          this.setData({ error: "请先登录，再录下这段记忆。已写文字会保留。" });
          wx.navigateTo({ url: "/pages/login/index?from=response" });
        } else {
          this.setData({ error: error instanceof Error ? error.message : "语音暂时不可用，请重试" });
          if (error instanceof ApiError && [404, 409, 410].includes(error.statusCode)) this.triggerEvent("unavailable", { error });
        }
      } finally { if (isCurrent()) this.setData({ busy: false }); }
    },
    voiceSource(event: { detail: { captureId?: string } }) {
      const id = String(event.detail.captureId || "");
      if (this.properties.active && /^[0-9a-f-]{36}$/.test(id)) this.setData({ voiceCaptureId: id, gatewayUrl: "", voiceStartRequest: 0 });
    },
    replaceVoice() {
      if (this.data.busy) return;
      this.setData({ voiceCaptureId: "", gatewayUrl: "", voiceStartRequest: 0,
        error: "原音已移除，文字保留。可以重新录音。" });
    },
    async submit() {
      const content = this.data.content.trim();
      if (!this.properties.active || this.data.busy || !content) return;
      runtime(this).targetReadingToken = this.properties.readingToken;
      runtime(this).targetVideoCode = this.properties.videoCode;
      const generation = runtime(this).generation;
      const fingerprint = `${content}\0${this.data.voiceCaptureId}`;
      if (!runtime(this).requestId || runtime(this).requestContent !== fingerprint) {
        runtime(this).requestId = `response-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        runtime(this).requestContent = fingerprint;
      }
      runtime(this).submitting = true;
      this.setData({ busy: true, error: "" });
      try {
        const target = this.properties.readingToken ? { reading_token: this.properties.readingToken } : { video_code: this.properties.videoCode };
        await submitShareResponse(target, content, runtime(this).requestId, this.data.voiceCaptureId);
        if (generation !== runtime(this).generation) return;
        runtime(this).requestId = ""; runtime(this).requestContent = "";
        this.setData({ sent: true, open: false, content: "", voiceCaptureId: "", gatewayUrl: "", voiceStartRequest: 0 });
      } catch (error) {
        if (generation !== runtime(this).generation) return;
        if (error instanceof ApiError && error.statusCode === 401) {
          this.setData({ error: "请先登录，再提交这段回应。文字已保留。" });
          wx.navigateTo({ url: "/pages/login/index?from=response" });
        } else if (error instanceof ApiError && error.statusCode === 410 && this.data.voiceCaptureId) {
          this.setData({ voiceCaptureId: "", gatewayUrl: "", voiceStartRequest: 0,
            error: "原音已过期，请重新录音；文字已保留，也可以直接发送文字。" });
        } else {
          this.setData({ error: error instanceof Error ? error.message : "暂时没能提交，请重试" });
          if (error instanceof ApiError && [404, 409, 410].includes(error.statusCode)) this.triggerEvent("unavailable", { error });
        }
      } finally {
        if (generation === runtime(this).generation) {
          runtime(this).submitting = false;
          this.setData({ busy: false });
        }
      }
    },
  },
});
