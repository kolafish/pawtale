const assert = require("node:assert/strict");
const { after, before, test } = require("node:test");
const {
  elementById,
  launchE2ESession,
  shutdownE2ESession,
} = require("../support/session");
const { waitForPageData } = require("../support/wait");
const {
  FIXTURE_ASSISTANT_TEXT,
  FIXTURE_SESSION_ID,
  FIXTURE_USER_TEXT,
  createVoiceFixtureServer,
} = require("../support/voice-fixture-server");

const BACKEND_COOKIE_STORAGE_KEY = "pawtale_backend_cookie";
const VOICE_AUTHORIZATION_STORAGE_KEY = "pawtale_voice_authorization_consent";
const VOICE_AUTHORIZATION_VERSION = "2026-06-18";

let fixture;
let session;
let failed = false;

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
  await session.miniProgram.mockWxMethod("getRecorderManager", deterministicRecorderManager);
  session.artifacts.mark("voice:setup:recorder-mocked");
  await session.miniProgram.mockWxMethod("getPrivacySetting", { needAuthorization: false });
  session.artifacts.mark("voice:setup:privacy-mocked");
  await session.miniProgram.mockWxMethod("showModal", { confirm: false, cancel: true });
  session.artifacts.mark("voice:setup:modal-mocked");
});

after(async () => {
  if (session) {
    for (const method of ["getRecorderManager", "getPrivacySetting", "showModal"]) {
      try {
        await session.miniProgram.restoreWxMethod(method);
      } catch {
        // The automation connection may already be closing.
      }
    }
    await session.artifacts.finish({
      failed,
      fixtureUnexpectedRequests: fixture?.state.unexpectedRequests || [],
      target: "devtools-voice-fixture",
    });
    await shutdownE2ESession(session);
  }
  await fixture?.close();
});

test("deterministic voice journey records, transcribes, answers, and plays TTS", async () => {
  try {
    session.artifacts.mark("voice:page:open:start");
    const page = await session.miniProgram.reLaunch(
      `/pages/voice-chat/index?voiceSessionId=${FIXTURE_SESSION_ID}`
      + "&mode=free_narration&interviewType=ai_interview"
      + "&sessionGoal=open_narration&projectTitle=E2E%20%E5%9B%9E%E5%BF%86%E5%BD%95",
    );
    const ready = await waitForPageData(
      page,
      (data) => data.status === "ready" && data.connecting === false,
      "确定性语音 gateway 就绪",
      30000,
    );
    assert.equal(ready.voiceAuthorizationAgreed, true);
    session.artifacts.mark("voice:page:open:complete");

    await (await elementById(page, "voice-primary-action")).tap();
    const recording = await waitForPageData(
      page,
      (data) => data.recording === true
        && data.captureFrameCount > 0
        && data.liveText.includes("第一次坐火车"),
      "录音帧和 ASR partial 到达页面",
      20000,
    );
    assert.equal(recording.status, "listening");
    session.artifacts.mark("voice:asr:partial");

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

    const gatewayTypes = fixture.state.gatewayClientEvents.map((event) => event.type);
    assert.deepEqual(gatewayTypes.slice(0, 3), ["session.start", "audio.frame", "audio.end"]);
    assert.equal(
      fixture.state.assistantClientEvents.some((event) => event.type === "turn.start"),
      true,
    );
    assert.deepEqual(fixture.state.unexpectedRequests, []);

    await (await elementById(page, "voice-finish-action")).tap();
    const stopped = await waitForPageData(
      page,
      (data) => data.status === "closed" && data.persistenceStateText === "已保存",
      "语音会话停止并显示已保存",
      20000,
    );
    assert.equal(stopped.primaryActionText, "已保存");
    assert.equal(
      fixture.state.gatewayClientEvents.at(-1)?.type,
      "session.stop",
    );
    session.artifacts.mark("voice:session:stopped");
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
