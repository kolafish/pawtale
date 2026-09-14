const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const {
  captureStorageEntries,
  elementById,
  launchE2ESession,
  restoreStorageEntries,
  shutdownE2ESession,
} = require("../support/session");
const { waitForPageData, waitForValue } = require("../support/wait");
const {
  FIXTURE_ASSISTANT_TEXT,
  FIXTURE_USER_TEXT,
  createVoiceFixtureServer,
} = require("../support/voice-fixture-server");

const BACKEND_COOKIE_STORAGE_KEY = "pawtale_backend_cookie";
const AI_MODEL_STORAGE_KEY = "pawtale_ai_model_settings";
const TTS_SETTINGS_STORAGE_KEY = "pawtale_memoir_tts_settings";
const VOICE_AUTHORIZATION_STORAGE_KEY = "pawtale_voice_authorization_consent";
const VOICE_AUTHORIZATION_VERSION = "2026-06-18";
const FIXTURE_AI_MODEL = "deepseek-v4-flash";
const FIXTURE_TTS_VOICE = "zh_male_m191_uranus_bigtts";
const FIXTURE_TTS_SPEED_RATIO = 0.7;

let fixture;
let session;
let failed = false;
let originalVoicePreferences = [];

function deterministicRecorderManager() {
  const handlers = {};
  let started = false;
  return {
    onStart(callback) {
      handlers.start = callback;
    },
    onFrameRecorded(callback) {
      handlers.frame = callback;
    },
    onError(callback) {
      handlers.error = callback;
    },
    onStop(callback) {
      handlers.stop = callback;
    },
    start() {
      started = true;
      setTimeout(() => {
        if (!started) {
          return;
        }
        handlers.start?.();
        const frameBuffer = new ArrayBuffer(640);
        const samples = new Int16Array(frameBuffer);
        samples.fill(12000);
        handlers.frame?.({ frameBuffer, isLastFrame: false });
      }, 20);
    },
    stop() {
      if (!started) {
        return;
      }
      started = false;
      setTimeout(() => handlers.stop?.({ tempFilePath: "e2e-recorder.pcm" }), 0);
    },
  };
}

before(async () => {
  fixture = createVoiceFixtureServer();
  const endpoints = await fixture.start();
  process.env.PAWTALE_E2E_EMAIL ||= "fixture@example.invalid";
  process.env.PAWTALE_E2E_PASSWORD ||= "fixture-password";
  process.env.PAWTALE_E2E_HTTP_BASE_URL = endpoints.httpBaseUrl;
  process.env.PAWTALE_E2E_WS_BASE_URL = endpoints.wsBaseUrl;
  session = await launchE2ESession();
  session.artifacts.mark("voice:setup:session-launched");
  originalVoicePreferences = await captureStorageEntries(session.miniProgram, [
    AI_MODEL_STORAGE_KEY,
    TTS_SETTINGS_STORAGE_KEY,
    VOICE_AUTHORIZATION_STORAGE_KEY,
    "pawtale_home_launch_state",
    "pawtale_accessibility_settings",
  ]);
  session.artifacts.mark("voice:setup:preferences-captured");
  await session.miniProgram.callWxMethod(
    "setStorageSync",
    BACKEND_COOKIE_STORAGE_KEY,
    "pawtale_session=e2e-fixture",
  );
  session.artifacts.mark("voice:setup:cookie-stored");
  await session.miniProgram.callWxMethod(
    "setStorageSync",
    VOICE_AUTHORIZATION_STORAGE_KEY,
    {
      agreed: true,
      version: VOICE_AUTHORIZATION_VERSION,
      agreedAt: "2026-08-25T00:00:00.000Z",
    },
  );
  session.artifacts.mark("voice:setup:consent-stored");
  await session.miniProgram.callWxMethod(
    "setStorageSync",
    AI_MODEL_STORAGE_KEY,
    { model: FIXTURE_AI_MODEL },
  );
  await session.miniProgram.callWxMethod(
    "setStorageSync",
    TTS_SETTINGS_STORAGE_KEY,
    {
      chat: { voice: FIXTURE_TTS_VOICE, speedRatio: FIXTURE_TTS_SPEED_RATIO },
      book: { voice: "", speedRatio: 1 },
    },
  );
  session.artifacts.mark("voice:setup:model-settings-stored");
  await session.miniProgram.mockWxMethod("getRecorderManager", deterministicRecorderManager);
  session.artifacts.mark("voice:setup:recorder-mocked");
  await session.miniProgram.mockWxMethod("getPrivacySetting", { needAuthorization: false });
  session.artifacts.mark("voice:setup:privacy-mocked");
  await session.miniProgram.mockWxMethod("showModal", { confirm: false, cancel: true });
  session.artifacts.mark("voice:setup:modal-mocked");
  fixture.reset();
  session.artifacts.mark("voice:setup:fixture-reset");
});

after(async () => {
  let teardownError = null;
  try {
    if (session) {
      try {
        await restoreStorageEntries(session.miniProgram, originalVoicePreferences);
        assert.deepEqual(
          await captureStorageEntries(
            session.miniProgram,
            originalVoicePreferences.map((entry) => entry.key),
          ),
          originalVoicePreferences,
        );
        session.artifacts.mark("voice:teardown:preferences-restored");
      } catch (error) {
        teardownError = error;
      }
      for (const method of ["getRecorderManager", "getPrivacySetting", "showModal"]) {
        try {
          await session.miniProgram.restoreWxMethod(method);
        } catch {
          // The automation connection may already be closing.
        }
      }
      try {
        await shutdownE2ESession(session);
      } catch (error) {
        teardownError = teardownError
          ? new AggregateError(
              [teardownError, error],
              "语音偏好与运行时存储恢复均失败",
            )
          : error;
      }
      try {
        await session.artifacts.finish({
          failed: failed || Boolean(teardownError),
          fixtureUnexpectedRequests: fixture?.state.unexpectedRequests || [],
          target: "devtools-voice-fixture",
          teardownError,
        });
      } catch (error) {
        teardownError = teardownError
          ? new AggregateError([teardownError, error], "测试清理与报告写入均失败")
          : error;
      }
    }
  } finally {
    try {
      await fixture?.close();
    } catch (error) {
      teardownError = teardownError
        ? new AggregateError([teardownError, error], "测试清理与 fixture 关闭均失败")
        : error;
    }
  }
  if (teardownError) {
    throw teardownError;
  }
});

test("deterministic voice journey records, transcribes, answers, and plays TTS", async () => {
  try {
    session.artifacts.mark("voice:page:open:start");
    const host = await session.miniProgram.reLaunch("/pages/home/index");
    const home = await waitForPageData(host, data => data.hasProject && !data.loading, "首页加载");
    if (home.showLaunchPanel) await (await host.$(".launch-secondary-button")).tap();
    await (await elementById(host, "primary-voice-action")).tap();
    await waitForPageData(host, data => Boolean(data.voiceEntry), "首页原位展开讲述");
    assert.deepEqual((await session.miniProgram.pageStack()).map(page => page.path), ["pages/home/index"]);
    assert.equal(fixture.state.createdSessions, 1);
    const page = await elementById(host, "voice-session");
    const ready = await waitForPageData(
      page,
      (data) => data.status === "ready" && data.connecting === false,
      "确定性语音 gateway 就绪",
      30000,
    );
    assert.equal(ready.voiceAuthorizationAgreed, true);
    session.artifacts.mark("voice:page:open:complete");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-ready.png") });

    await (await elementById(page, "voice-primary-action")).tap();
    const recording = await waitForPageData(
      page,
      (data) => data.recording === true
        && fixture.state.activeGatewayClientEvents.some(event => event.type === "audio.frame")
        && data.liveText.includes("第一次坐火车"),
      "录音帧和 ASR partial 到达页面",
      20000,
    );
    assert.equal(recording.status, "listening");
    session.artifacts.mark("voice:asr:partial");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-listening.png") });

    await (await elementById(page, "voice-primary-action")).tap();
    const completed = await waitForPageData(
      page,
      (data) => data.followUpLoading === false
        && data.followUpTTSLoading === false
        && data.transcriptLines.some(
          (line) => line.role === "user" && line.text === FIXTURE_USER_TEXT,
        )
        && data.transcriptLines.some(
          (line) => line.role === "assistant" && line.text === FIXTURE_ASSISTANT_TEXT,
        ),
      "ASR final、LLM 回应和 TTS 播放完成",
      30000,
    );
    assert.equal(completed.followUpTTSError, "");
    assert.equal(completed.ttsProgressPercent, 100);
    assert.equal(completed.persistenceStateText, "小余已回应");
    session.artifacts.mark("voice:assistant:complete");

    const activeGatewayEvents = fixture.state.activeGatewayClientEvents;
    const gatewayTypes = activeGatewayEvents.map((event) => event.type);
    assert.deepEqual(gatewayTypes.slice(0, 3), ["session.start", "audio.frame", "audio.end"]);
    const sessionStart = activeGatewayEvents.find(
      (event) => event.type === "session.start",
    );
    assert.ok(sessionStart, "ASR gateway 应收到 session.start");
    assert.equal(sessionStart.guidance_mode, "free_narration");
    assert.equal(sessionStart.speaker_role, "narrator");
    assert.equal(sessionStart.ai_model, FIXTURE_AI_MODEL);
    assert.equal(typeof activeGatewayEvents[1].audio, "string");
    const turnStart = fixture.state.assistantClientEvents.find(
      (event) => event.type === "turn.start",
    );
    assert.ok(turnStart, "assistant stream 应收到 turn.start");
    assert.equal(turnStart.audio_protocol, "pcm_frame");
    assert.equal(turnStart.ai_model, FIXTURE_AI_MODEL);
    assert.equal(turnStart.regenerate, false);
    assert.equal(turnStart.voice, FIXTURE_TTS_VOICE);
    assert.equal(turnStart.speed_ratio, FIXTURE_TTS_SPEED_RATIO);
    assert.deepEqual(fixture.state.unexpectedRequests, []);

    await (await elementById(page, "voice-finish-action")).tap();
    await waitForValue(() => fixture.state.sessionStatus, status => status === "stopped", {
      description: "语音会话确认停止", timeoutMs: 20000,
    });
    assert.equal(
      activeGatewayEvents.at(-1)?.type,
      "session.stop",
    );
    await waitForPageData(host, data => !data.voiceEntry && data.narrationSaved, "同一首页显示讲述已保存");
    assert.deepEqual((await session.miniProgram.pageStack()).map(page => page.path), ["pages/home/index"]);
    session.artifacts.mark("voice:session:stopped");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-saved.png") });

    await session.miniProgram.callWxMethod("setStorageSync", "pawtale_accessibility_settings", { largeTextEnabled: true, autoBargeInEnabled: false });
    await host.callMethod("refreshAccessibilitySettings");
    await (await elementById(host, "primary-voice-action")).tap();
    const next = await elementById(host, "voice-session");
    const nextReady = await waitForPageData(next, data => data.status === "ready", "第二次讲述就绪");
    assert.equal(fixture.state.createdSessions, 2);
    assert.equal(nextReady.largeTextEnabled, true);
    assert.equal(nextReady.liveText, "");
    assert.equal(nextReady.transcriptLines.some(line => line.text === FIXTURE_USER_TEXT), false);
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-large-text.png") });
    await session.miniProgram.navigateTo("/pages/voice-authorization/index");
    await session.miniProgram.navigateBack();
    assert.equal((await host.data()).voiceEntry.voiceSessionId, fixture.state.sessionId);
    assert.equal((await next.data()).recording, false);
    await (await elementById(next, "voice-finish-action")).tap();
    await waitForPageData(host, data => !data.voiceEntry, "结束空讲述并收起");
    assert.deepEqual(fixture.state.unexpectedRequests, []);
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
