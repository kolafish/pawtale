const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
require("../../../miniprogram/test/register-ts");
const {
  VOICE_AUTHORIZATION_STORAGE_KEY,
  VOICE_AUTHORIZATION_VERSION,
} = require("../../../miniprogram/services/voice-authorization.ts");
const {
  captureStorageEntries,
  elementById,
  launchE2ESession,
  restoreStorageEntries,
  shutdownE2ESession,
} = require("../support/session");
const { waitForCurrentPage, waitForPageData, waitForValue } = require("../support/wait");
const {
  FIXTURE_ASSISTANT_TEXT,
  FIXTURE_PROJECT_ID,
  FIXTURE_USER_TEXT,
  createVoiceFixtureServer,
} = require("../support/voice-fixture-server");

const BACKEND_COOKIE_STORAGE_KEY = "pawtale_backend_cookie";
const AI_MODEL_STORAGE_KEY = "pawtale_ai_model_settings";
const TTS_SETTINGS_STORAGE_KEY = "pawtale_memoir_tts_settings";
const FIXTURE_AI_MODEL = "deepseek-flash";
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
  session = await launchE2ESession({ initialPage: "/pages/login/index" });
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
  await session.miniProgram.callWxMethod("setStorageSync", "pawtale_memoir_session", {
    cache_identity: { user_id: "e2e-user" },
  });
  session.artifacts.mark("voice:setup:cookie-stored");
  await session.miniProgram.callWxMethod(
    "setStorageSync",
    VOICE_AUTHORIZATION_STORAGE_KEY,
    {
      userId: "e2e-user",
      consent: {
        agreed: true,
        version: VOICE_AUTHORIZATION_VERSION,
        agreedAt: "2026-08-25T00:00:00.000Z",
      },
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
  await session.miniProgram.callWxMethod("setStorageSync", "pawtale_accessibility_settings", {
    largeTextEnabled: false, autoBargeInEnabled: true,
  });
  session.artifacts.mark("voice:setup:model-settings-stored");
  await session.miniProgram.mockWxMethod("getRecorderManager", deterministicRecorderManager);
  session.artifacts.mark("voice:setup:recorder-mocked");
  await session.miniProgram.mockWxMethod("getPrivacySetting", { needAuthorization: false });
  session.artifacts.mark("voice:setup:privacy-mocked");
  await session.miniProgram.mockWxMethod("getSetting", {
    authSetting: { "scope.record": true },
  });
  session.artifacts.mark("voice:setup:recorder-permission-mocked");
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
      for (const method of ["getRecorderManager", "getPrivacySetting", "getSetting", "openSetting", "showModal"]) {
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

async function tapBoundAction(element) {
  // Exercise the WXML binding without Developer Tools' coordinate-based tap,
  // which can target a different row after large-text layout or page scrolling.
  await element.trigger("tap");
}

async function assertVerticallyCentered(container, label, description, tolerance = 2) {
  const [containerOffset, containerSize, labelOffset, labelSize] = await Promise.all([
    container.offset(),
    container.size(),
    label.offset(),
    label.size(),
  ]);
  const containerCenter = containerOffset.top + (containerSize.height / 2);
  const labelCenter = labelOffset.top + (labelSize.height / 2);
  const delta = Math.abs(containerCenter - labelCenter);
  assert.ok(
    delta <= tolerance,
    `${description}垂直居中（中心偏差 ${delta.toFixed(2)}px）`,
  );
}

async function assertMeasuredTranscriptScroll(page, expectedText, description) {
  const data = await waitForPageData(
    page,
    value => value.liveText === expectedText,
    `${description}文字更新`,
  );
  const [viewportSize, contentSize] = await Promise.all([
    (await page.$(".live-scroll")).size(),
    (await page.$(".live-text-content")).size(),
  ]);
  const expectedScrollTop = Math.max(0, Math.ceil(contentSize.height - viewportSize.height));
  const measured = await waitForPageData(
    page,
    value => Math.abs(value.liveScrollTop - expectedScrollTop) <= 1,
    `${description}跟随真实行高`,
  );
  assert.equal(measured.liveTranscriptFollowing, true);
  assert.ok(
    Math.abs(measured.liveScrollTop - expectedScrollTop) <= 1,
    `${description}滚动位置来自渲染高度（实际 ${measured.liveScrollTop}，期望 ${expectedScrollTop}）`,
  );
  return {
    contentHeight: contentSize.height,
    scrollTop: measured.liveScrollTop,
    viewportHeight: viewportSize.height,
    initialData: data,
  };
}

async function changeProjectDefaultMode(host, mode) {
  const edit = await session.miniProgram.navigateTo(
    `/pages/project-edit/index?projectId=${encodeURIComponent(FIXTURE_PROJECT_ID)}`,
  );
  await waitForPageData(edit, data => !data.loading && data.projectId === FIXTURE_PROJECT_ID, "加载回忆录设置");
  await tapBoundAction(await elementById(edit, `default-mode-${mode}`));
  await waitForPageData(edit, data => data.defaultNarrationMode === mode, "修改默认访谈方式");
  await tapBoundAction(await elementById(edit, "project-save-action"));
  await waitForCurrentPage(session.miniProgram, "pages/home/index");
  await host.callMethod("loadHome", true);
  await waitForPageData(host, data => data.selectedMode === mode && !data.loading, "首页读取默认访谈方式");
}

test("deterministic voice journey records, transcribes, answers, and plays TTS", async () => {
  let lastVoiceData;
  try {
    session.artifacts.mark("voice:page:open:start");
    const host = await session.miniProgram.reLaunch("/pages/home/index");
    await waitForPageData(host, data => data.hasProject && !data.loading, "首页加载");
    assert.equal(await host.$("#home-narration-modes"), null);
    assert.equal((await host.data()).selectedMode, "free_narration");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-project-default-mode.png") });
    await tapBoundAction(await elementById(host, "primary-voice-action"));
    await waitForPageData(host, data => Boolean(data.voiceEntry), "首页原位展开讲述");
    assert.deepEqual((await session.miniProgram.pageStack()).map(page => page.path), ["pages/home/index"]);
    assert.equal(fixture.state.createdSessions, 1);
    let page = await elementById(host, "voice-session");
    const ready = await waitForPageData(
      page,
      (data) => data.status === "ready",
      "确定性语音 gateway 就绪",
      30000,
    );
    assert.equal(ready.voiceAuthorizationAgreed, true);
    assert.equal(ready.recoveryNotice, "");
    assert.equal(ready.transcriptLines.some(line => line.text === FIXTURE_USER_TEXT), false);
    assert.ok(await page.$(".assistant-panel"), "提问态只显示小余的问题卡");
    assert.equal(await page.$(".transcript-stage"), null, "提问态不再显示重复教学卡");
    assert.equal(await page.$(".history-panel"), null, "只有小余开场白时不显示已保存记录");
    const readyAssistantButtons = await page.$$(".assistant-button");
    assert.equal(readyAssistantButtons.length, 2, "提问态只保留重听和换问法两个辅助操作");
    assert.match(await readyAssistantButtons[0].text(), /重听一遍/);
    await assertVerticallyCentered(
      await page.$(".assistant-label"),
      await page.$(".assistant-label-text"),
      "小余提示标签",
    );
    const readyPrimaryAction = await elementById(page, "voice-primary-action");
    const readyPrimarySize = await readyPrimaryAction.size();
    assert.ok(readyPrimarySize.width >= 40 && readyPrimarySize.height >= 40, "主操作满足最小触控区域");
    const topBar = await page.$(".top-bar");
    const topBarOffset = await topBar.offset();
    const topBarSize = await topBar.size();
    const finishAction = await elementById(page, "voice-finish-action");
    const homeActionSize = await (await elementById(page, "voice-home-action")).size();
    const finishActionOffset = await finishAction.offset();
    const finishActionSize = await finishAction.size();
    assert.ok(
      Math.abs(homeActionSize.width - finishActionSize.width) <= 1,
      `顶部左右操作等宽，模式标题保持居中（首页 ${homeActionSize.width}，结束 ${finishActionSize.width}）`,
    );
    assert.ok(
      finishActionOffset.left + finishActionSize.width <= topBarOffset.left + topBarSize.width + 1,
      `结束讲述按钮完整位于顶部栏内（按钮右侧 ${finishActionOffset.left + finishActionSize.width}，顶部栏右侧 ${topBarOffset.left + topBarSize.width}）`,
    );
    const readyMicIconSize = await (await page.$(".mic-icon")).size();
    assert.ok(
      readyMicIconSize.width >= 32 && readyMicIconSize.height >= 32,
      `简洁麦克风在按钮内保持清晰尺寸（${readyMicIconSize.width}×${readyMicIconSize.height}）`,
    );
    assert.equal(await page.$("#session-narration-modes"), null);
    assert.equal(await (await page.$(".mode-label")).text(), "自由讲述");
    session.artifacts.mark("voice:page:open:complete");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-ready.png") });

    await session.miniProgram.mockWxMethod("getSetting", { authSetting: { "scope.record": false } });
    await tapBoundAction(await elementById(page, "voice-primary-action"));
    await waitForPageData(page, data => data.recorderSettingsRequired && !data.recordingStarting, "拒绝麦克风权限显示设置入口");
    const gatewayCount = fixture.state.gatewayConnections.length;
    await session.miniProgram.mockWxMethod("getSetting", { authSetting: { "scope.record": true } });
    await session.miniProgram.mockWxMethod("openSetting", { authSetting: { "scope.record": true } });
    await tapBoundAction(await elementById(page, "voice-recorder-settings"));
    await waitForPageData(page, data => !data.recorderSettingsRequired && data.status === "ready", "从设置返回恢复录音操作");
    assert.equal(fixture.state.gatewayConnections.length, gatewayCount, "权限恢复保留健康的语音连接");
    assert.equal(fixture.state.createdSessions, 1);
    session.artifacts.mark("voice:permission:recovered");

    const endedSessionId = (await host.data()).voiceEntry.voiceSessionId;
    await tapBoundAction(await elementById(page, "voice-home-action"));
    await waitForValue(() => fixture.state.sessionStatus, status => status === "stopped", {
      description: "首页入口确认结束语音会话", timeoutMs: 20000,
    });
    await waitForPageData(host, data => !data.voiceEntry, "结束讲述并返回首页");
    assert.equal(fixture.state.createdSessions, 1);
    assert.equal(await host.$("#voice-session"), null, "返回首页后不保留暂停会话");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-ended.png") });

    await tapBoundAction(await elementById(host, "primary-voice-action"));
    const restartedHome = await waitForPageData(host, data => data.voiceEntry?.voiceSessionId, "从首页开始新的讲述");
    page = await elementById(host, "voice-session");
    await waitForPageData(
      page,
      data => data.status === "ready",
      "新语音 gateway 就绪",
      30000,
    );
    assert.notEqual(restartedHome.voiceEntry.voiceSessionId, endedSessionId);
    assert.equal(fixture.state.createdSessions, 2, "返回首页后的下一次讲述会新建会话");

    await tapBoundAction(await elementById(page, "voice-primary-action"));
    const recording = await waitForPageData(
      page,
      (data) => data.recording === true
        && fixture.state.activeGatewayClientEvents.some(event => event.type === "audio.frame")
        && data.liveText.includes("第一次坐火车"),
      "录音帧和 ASR partial 到达页面",
      20000,
    );
    assert.equal(recording.status, "listening");
    assert.equal(await page.$(".assistant-panel"), null, "讲述态不再保留小余问题卡");
    const recordingTranscriptStage = await page.$(".transcript-stage");
    assert.ok(recordingTranscriptStage, "讲述态以实时文字卡为主角");
    assert.equal(await page.$(".live-text-context"), null, "讲述态不再拆出容易误解的灰色上一段");
    assert.equal((await page.$$(".assistant-button")).length, 0, "讲述态不显示提问辅助操作");
    const recordingStageOffset = await recordingTranscriptStage.offset();
    const recordingStageSize = await recordingTranscriptStage.size();
    const recordingDock = await page.$(".control-bar");
    const recordingDockOffset = await recordingDock.offset();
    assert.ok(
      recordingStageOffset.top + recordingStageSize.height <= recordingDockOffset.top,
      `实时文字卡不被固定操作区遮挡（卡片底部 ${recordingStageOffset.top + recordingStageSize.height}，操作区顶部 ${recordingDockOffset.top}）`,
    );
    assert.equal(await page.$("#session-narration-modes"), null);
    await assertVerticallyCentered(
      await page.$(".stage-status"),
      await page.$(".stage-status-text"),
      "正在倾听状态标签",
    );
    const longTranscript = "那时候每天早上起来以后，我先收拾屋子，再去院子里忙一会儿。".repeat(12);
    const transcriptSnapshots = [];
    for (const length of [40, 80, 120, 200, 300]) {
      const partial = longTranscript.slice(0, length);
      await page.callMethod("handlePartial", partial);
      transcriptSnapshots.push(await assertMeasuredTranscriptScroll(
        page,
        partial,
        `${length}字实时讲述`,
      ));
    }
    assert.ok(
      transcriptSnapshots.every((snapshot, index) => (
        index === 0 || snapshot.scrollTop >= transcriptSnapshots[index - 1].scrollTop
      )),
      "逐步增长的实时文字不会反向跳动",
    );
    let sameLineVerified = false;
    let priorSnapshot = transcriptSnapshots.at(-1);
    for (let length = 301; length <= 310; length += 1) {
      const partial = longTranscript.slice(0, length);
      await page.callMethod("handlePartial", partial);
      const nextSnapshot = await assertMeasuredTranscriptScroll(page, partial, `${length}字同行增量`);
      if (nextSnapshot.contentHeight === priorSnapshot.contentHeight) {
        assert.equal(nextSnapshot.scrollTop, priorSnapshot.scrollTop, "同一渲染行内新增文字不会跳动");
        sameLineVerified = true;
        break;
      }
      priorSnapshot = nextSnapshot;
    }
    assert.equal(sameLineVerified, true, "找到并验证一组未换行的实时文字增量");
    const liveScroll = await page.$(".live-scroll");
    await liveScroll.trigger("touchstart");
    const pausedReview = await waitForPageData(
      page,
      data => data.liveTranscriptFollowing === false,
      "触摸实时文字后暂停自动跟随",
    );
    const pausedTop = pausedReview.liveScrollTop;
    const pausedUpdate = longTranscript.slice(0, 320);
    await page.callMethod("handlePartial", pausedUpdate);
    await waitForPageData(page, data => data.liveText === pausedUpdate, "阅读时继续接收实时文字");
    assert.equal((await page.data()).liveScrollTop, pausedTop, "用户阅读旧文字时不抢回滚动位置");
    await tapBoundAction(await page.$(".return-to-latest"));
    await assertMeasuredTranscriptScroll(page, pausedUpdate, "回到最新文字");
    const shorterRewrite = longTranscript.slice(0, 120);
    await page.callMethod("handlePartial", shorterRewrite);
    const shorterSnapshot = await assertMeasuredTranscriptScroll(
      page,
      shorterRewrite,
      "ASR 缩短重写",
    );
    assert.ok(
      shorterSnapshot.scrollTop < transcriptSnapshots.at(-1).scrollTop,
      "ASR 把内容改短后，跟随位置会按真实高度回收",
    );
    await page.callMethod("handlePartial", longTranscript.slice(0, 300));
    await assertMeasuredTranscriptScroll(page, longTranscript.slice(0, 300), "ASR 恢复长文本");
    session.artifacts.mark("voice:asr:partial");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-listening.png") });

    await tapBoundAction(await elementById(page, "voice-primary-action"));
    const responding = await waitForPageData(
      page,
      data => data.assistantPromptText === FIXTURE_ASSISTANT_TEXT
        && data.followUpTTSPlaying === true,
      "讲述结束后切换到正在播放的小余问题",
      20000,
    );
    assert.equal(responding.micButtonProcessing, false);
    assert.equal(responding.micButtonDisabled, false);
    assert.doesNotMatch(responding.micButtonClass, /processing/);
    assert.equal(responding.micButtonText, "我来讲了");
    assert.equal(responding.showAssistantPanel, true);
    assert.equal(responding.showTranscriptStage, false);
    assert.ok(await page.$(".assistant-panel"), "AI 开始播放后立即显示本轮问题");
    assert.equal(await page.$(".transcript-stage"), null, "AI 播放时不再停留在上一段整理卡");
    const completed = await waitForPageData(
      page,
      (data) => {
        lastVoiceData = data;
        return data.followUpLoading === false
        && data.followUpTTSLoading === false
        && data.ttsProgressPercent === 100
        && data.transcriptLines.some(
          (line) => line.role === "user" && line.text === FIXTURE_USER_TEXT,
        )
        && data.transcriptLines.some(
          (line) => line.role === "assistant" && line.text === FIXTURE_ASSISTANT_TEXT,
        );
      },
      "ASR final、LLM 回应和 TTS 播放完成",
      30000,
    );
    assert.equal(completed.followUpTTSError, "");
    assert.equal(completed.ttsProgressPercent, 100);
    assert.equal(completed.persistenceStateText, "小余已回应");
    assert.ok(await page.$(".history-panel"), "完成一段讲述后显示轻量保存状态条");
    assert.ok(await page.$(".history-saved-check"), "保存状态条包含确认标记");
    await tapBoundAction(await page.$(".history-toggle"));
    await waitForPageData(page, data => data.historyExpanded, "展开已保存讲述");
    await assertVerticallyCentered(
      await page.$(".line-role"),
      await page.$(".line-role-text"),
      "讲述角色标签",
    );
    session.artifacts.mark("voice:assistant:complete");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-completed.png") });

    const activeGatewayEvents = fixture.state.activeGatewayClientEvents;
    const gatewayTypes = activeGatewayEvents.map((event) => event.type);
    assert.deepEqual(gatewayTypes.slice(0, 3), ["session.start", "audio.frame", "audio.end"]);
    assert.ok(gatewayTypes.includes("barge_in.start"), "播放期间通过现有 gateway 启动监听");
    assert.ok(gatewayTypes.includes("barge_in.frame"), "监听 PCM 帧进入 gateway");
    assert.ok(gatewayTypes.includes("barge_in.stop"), "播放结束时停止监听");
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

    await tapBoundAction(await elementById(page, "voice-finish-action"));
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
    await changeProjectDefaultMode(host, "guided_interview");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "project-default-guided.png") });
    await tapBoundAction(await elementById(host, "primary-voice-action"));
    const next = await elementById(host, "voice-session");
    const nextReady = await waitForPageData(next, data => data.status === "ready", "下一次讲述就绪");
    assert.equal(fixture.state.createdSessions, 3);
    assert.equal(nextReady.largeTextEnabled, true);
    assert.equal(nextReady.guidanceMode, "guided_interview");
    assert.equal(await next.$("#session-narration-modes"), null);
    assert.equal(await (await next.$(".mode-label")).text(), "引导访谈");
    assert.equal(nextReady.liveText, "");
    assert.equal(nextReady.transcriptLines.some(line => line.text === FIXTURE_USER_TEXT), false);
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "home-voice-large-text.png") });
    await tapBoundAction(await elementById(next, "voice-primary-action"));
    await waitForPageData(
      next,
      data => data.recording && data.liveText.includes("第一次坐火车"),
      "大字模式开始实时讲述",
    );
    const largeTranscript = "小时候家门口有一条小路，每次出门都能遇见熟悉的人。".repeat(14).slice(0, 300);
    await next.callMethod("handlePartial", largeTranscript);
    await assertMeasuredTranscriptScroll(next, largeTranscript, "大字模式300字实时讲述");
    await assertVerticallyCentered(
      await next.$(".stage-status"),
      await next.$(".stage-status-text"),
      "大字模式正在倾听状态标签",
    );
    const largeStage = await next.$(".transcript-stage");
    const largeDock = await next.$(".control-bar");
    const [largeStageOffset, largeStageSize, largeDockOffset] = await Promise.all([
      largeStage.offset(),
      largeStage.size(),
      largeDock.offset(),
    ]);
    assert.ok(
      largeStageOffset.top + largeStageSize.height <= largeDockOffset.top,
      `大字模式的完整行窗口不被固定操作区遮挡（文字卡底部 ${largeStageOffset.top + largeStageSize.height}，固定操作区顶部 ${largeDockOffset.top}）`,
    );
    await tapBoundAction(await elementById(next, "voice-primary-action"));
    await waitForPageData(
      next,
      data => !data.recording && data.transcriptLines.some(line => line.text === FIXTURE_USER_TEXT),
      "大字模式讲述保存",
    );
    session.artifacts.mark("voice:guided:leave");
    await session.miniProgram.navigateTo("/pages/voice-authorization/index");
    session.artifacts.mark("voice:guided:return");
    await session.miniProgram.navigateBack();
    assert.equal((await host.data()).voiceEntry.voiceSessionId, fixture.state.sessionId);
    assert.equal((await next.data()).recording, false);
    assert.equal(await next.$("#session-narration-modes"), null);
    await tapBoundAction(await elementById(next, "voice-finish-action"));
    await waitForPageData(host, data => !data.voiceEntry, "结束空讲述并收起");
    await waitForPageData(host, data => !data.loading && !data.creatingVoice && !data.voiceEntry, "首页完成刷新");
    session.artifacts.mark("voice:human:start");
    await session.miniProgram.pageScrollTo(0);
    await changeProjectDefaultMode(host, "human_interview");
    await tapBoundAction(await elementById(host, "primary-voice-action"));
    const human = await elementById(host, "voice-session");
    await waitForPageData(human, data => data.status === "ready" && data.showHumanRecordActions, "真人访谈就绪");
    assert.equal(await human.$("#session-narration-modes"), null);
    assert.equal(await (await human.$(".mode-label")).text(), "真人访谈");
    await (await human.$(".role-record-button.narrator")).tap();
    await waitForPageData(human, data => data.recording && data.liveText.includes("第一次坐火车"), "真人录音转写");
    assert.equal(await human.$("#session-narration-modes"), null);
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "four-narration-modes-human-recording.png") });
    await tapBoundAction(await elementById(human, "voice-primary-action"));
    await waitForPageData(human, data => !data.recording && data.transcriptLines.some(line => line.text === FIXTURE_USER_TEXT), "真人讲述保存");
    await tapBoundAction(await elementById(human, "voice-finish-action"));
    await waitForPageData(host, data => !data.voiceEntry, "真人访谈结束");
    session.artifacts.mark("voice:supplement:select");
    await session.miniProgram.pageScrollTo(0);
    await host.callMethod("openReviewIssues");
    const supplement = await waitForCurrentPage(session.miniProgram, "pages/book-completion/index");
    await waitForPageData(supplement, data => !data.loading && !data.error, "书稿待补问题页");
    assert.equal(await supplement.$("#supplement-narration-modes"), null);
    assert.equal(fixture.state.createdSessions, 4, "补充方式先选问题，不提前开录音会话");
    await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, "book-completion-without-mode-picker.png") });
    await session.miniProgram.navigateBack();
    await waitForCurrentPage(session.miniProgram, "pages/home/index");
    await waitForPageData(host, data => data.selectedMode === "human_interview", "返回首页沿用项目默认访谈方式");
    assert.deepEqual(fixture.state.unexpectedRequests, []);
  } catch (error) {
    failed = true;
    session.artifacts.events.push({ kind: "voice-diagnostics", value: {
      data: lastVoiceData,
      assistantEvents: fixture.state.assistantClientEvents,
      gatewayEventTypes: fixture.state.gatewayClientEvents.map(event => event.type),
    } });
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
