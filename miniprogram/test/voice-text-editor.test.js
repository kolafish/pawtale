require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  componentContext,
  loadComponentModule,
  mockModule,
} = require("./page-harness");

test("shared voice text editor shows live transcription without replacing existing text", async () => {
  const controller = {
    callbacks: null,
    finishCount: 0,
    async connect(options) {
      this.callbacks = options.callbacks;
    },
    startRecording() {
      return true;
    },
    stopRecording() {},
    async finishSession() {
      this.finishCount += 1;
      return true;
    },
    close() {},
  };
  mockModule("../services/streaming-voice-controller.ts", {
    StreamingVoiceController: class {
      constructor() {
        return controller;
      }
    },
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => true,
    syncVoiceAuthorizationConsent: async () => {},
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
  });
  mockModule("../services/wechat-privacy.ts", {
    ensureRecorderPrivacyAuthorization: async () => {},
  });
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const component = loadComponentModule(
    "../components/voice-text-editor/index.ts",
  ).component;
  const context = componentContext(component, {
    value: "请保留校舍这一段。",
    gatewayUrl: "ws://backend.local/book-review/dictation",
  });
  component.lifetimes.attached.call(context);

  await context.toggleVoice.call(context);
  controller.callbacks.onStatus("listening");
  controller.callbacks.onPartial("人物称谓");
  controller.callbacks.onPartial("人物称谓要前后一致");
  controller.callbacks.onFinal({
    type: "asr.final",
    text: "人物称谓要前后一致。",
  });
  controller.callbacks.onEvent({ type: "audio.ended" });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(controller.finishCount, 1);
  assert.equal(context.data.voicePhase, "idle");
  assert.equal(
    context.events.filter((event) => event.name === "change").at(-1).detail.value,
    "请保留校舍这一段。\n人物称谓要前后一致。",
  );
  assert.deepEqual(toasts, ["语音已转成文字"]);
});
