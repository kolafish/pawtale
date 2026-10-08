require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  clearModule,
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");

test("transcript polish transport ignores stale project settings", () => {
  let isFresh = false;
  mockModule("../stores/memoir-cache.ts", {
    readMemoirIdentitySnapshot: () => ({
      identity: {
        active_project: {
          metadata_json: { transcript_polish_level: "high" },
        },
      },
      isFresh,
    }),
  });
  const modulePath = clearModule("../services/transcript-polish-settings.ts");
  const settings = require(modulePath);

  assert.equal(settings.readSelectedTranscriptPolishLevel(), undefined);
  isFresh = true;
  assert.equal(settings.readSelectedTranscriptPolishLevel(), "high");
});

function loadVoiceSettingsPage({
  aiModelExports = {},
  accessibilityExports = {},
  ttsExports = {},
  memoirCacheExports = {},
} = {}) {
  mockModule("../stores/memoir-cache.ts", {
    readMemoirIdentitySnapshot: () => ({
      identity: { role: "admin" },
      isFresh: true,
    }),
    ...memoirCacheExports,
  });
  mockModule("../services/ai-model-settings.ts", {
    AI_MODEL_OPTIONS: [
      {
        value: "deepseek-v4-pro",
        label: "DeepSeek V4 Pro",
        description: "质量优先",
      },
      {
        value: "doubao-seed-2-0-pro-260215",
        label: "Doubao Seed 2.0 Pro",
        description: "豆包旗舰模型",
      },
    ],
    aiModelLabel: (model) => (
      model === "doubao-seed-2-0-pro-260215" ? "Doubao Seed 2.0 Pro" : "DeepSeek V4 Pro"
    ),
    readSelectedAIModel: () => "deepseek-v4-pro",
    saveSelectedAIModel: (model) => model,
    ...aiModelExports,
  });
  mockModule("../services/accessibility-settings.ts", {
    accessibilityPageClass: (baseClass, largeTextEnabled) => (
      largeTextEnabled ? `${baseClass} large-text` : baseClass
    ),
    readAccessibilitySettings: () => ({
      largeTextEnabled: false,
      autoBargeInEnabled: true,
    }),
    saveAutoBargeInEnabled: (autoBargeInEnabled) => ({
      largeTextEnabled: false,
      autoBargeInEnabled,
    }),
    ...accessibilityExports,
  });
  mockModule("../services/tts-settings.ts", {
    BOOK_TTS_VOICE_OPTIONS: [
      { value: "", label: "默认音色", description: "温柔女声" },
      { value: "cixingnansheng", label: "磁性男声", description: "适合长篇朗读" },
    ],
    TTS_VOICE_OPTIONS: [
      {
        value: "",
        label: "默认音色",
        description: "使用后端当前默认配置",
      },
      {
        value: "zh_male_m191_uranus_bigtts",
        label: "通用男声",
        description: "适合访谈引导",
      },
    ],
    TTS_SPEED_OPTIONS: [
      {
        value: 1,
        label: "正常 1.0x",
        description: "使用默认语速",
      },
      {
        value: 1.5,
        label: "快速 1.5x",
        description: "适合快速浏览",
      },
    ],
    readTTSSettingsProfiles: () => ({
      chat: { voice: "", speedRatio: 1 },
      book: { voice: "", speedRatio: 1 },
    }),
    readScopedTTSSettings: () => ({ voice: "", speedRatio: 1 }),
    saveScopedTTSSettings: (_scope, settings) => settings,
    ...ttsExports,
  });

  return loadPageModule("../pages/voice-settings/index.ts");
}

test("voice settings page is registered in the miniprogram route list", () => {
  const appConfig = JSON.parse(fs.readFileSync(
    path.join(__dirname, "../app.json"),
    "utf8",
  ));

  assert.ok(appConfig.pages.includes("pages/voice-settings/index"));
  const pageTemplate = fs.readFileSync(
    path.join(__dirname, "../pages/voice-settings/index.wxml"),
    "utf8",
  );
  assert.doesNotMatch(pageTemplate, /润色强度|transcriptPolishOptions/);
  const meTemplate = fs.readFileSync(
    path.join(__dirname, "../pages/me/index.wxml"),
    "utf8",
  );
  assert.doesNotMatch(meTemplate, /文字润色/);
});

test("voice settings loads account and device preferences on show", () => {
  const { page } = loadVoiceSettingsPage({
    aiModelExports: {
      readSelectedAIModel: () => "doubao-seed-2-0-pro-260215",
    },
    accessibilityExports: {
      readAccessibilitySettings: () => ({
        largeTextEnabled: true,
        autoBargeInEnabled: false,
      }),
    },
    ttsExports: {
      readTTSSettingsProfiles: () => ({
        chat: { voice: "zh_male_m191_uranus_bigtts", speedRatio: 1.5 },
        book: { voice: "", speedRatio: 1 },
      }),
    },
  });
  const context = pageContext(page);

  page.onShow.call(context);

  assert.equal(context.data.pageClass, "voice-settings-page large-text");
  assert.equal(context.data.autoBargeInEnabled, false);
  assert.equal(context.data.aiModel, "doubao-seed-2-0-pro-260215");
  assert.equal(context.data.aiModelLabel, "Doubao Seed 2.0 Pro");
  assert.equal(context.data.chatTTSVoice, "zh_male_m191_uranus_bigtts");
  assert.equal(context.data.chatTTSSpeedRatio, 1.5);
  assert.equal(context.data.bookTTSVoice, "");
  assert.equal(context.data.bookTTSSpeedRatio, 1);
});

test("voice settings saves the automatic speech detection preference", () => {
  const saved = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const { page } = loadVoiceSettingsPage({
    accessibilityExports: {
      saveAutoBargeInEnabled: (autoBargeInEnabled) => {
        saved.push(autoBargeInEnabled);
        return { largeTextEnabled: true, autoBargeInEnabled };
      },
    },
  });
  const context = pageContext(page);

  page.changeAutoBargeIn.call(context, { detail: { value: false } });

  assert.deepEqual(saved, [false]);
  assert.equal(context.data.pageClass, "voice-settings-page large-text");
  assert.equal(context.data.autoBargeInEnabled, false);
  assert.deepEqual(toasts, ["自动检测已关闭"]);
});

test("voice settings saves the selected AI model", () => {
  const saved = [];
  global.wx = {
    showToast() {},
  };
  const { page } = loadVoiceSettingsPage({
    aiModelExports: {
      saveSelectedAIModel: (model) => {
        saved.push(model);
        return model;
      },
    },
  });
  const context = pageContext(page);

  page.selectAIModel.call(context, {
    currentTarget: {
      dataset: {
        model: "doubao-seed-2-0-pro-260215",
      },
    },
  });

  assert.deepEqual(saved, ["doubao-seed-2-0-pro-260215"]);
  assert.equal(context.data.aiModel, "doubao-seed-2-0-pro-260215");
  assert.equal(context.data.aiModelLabel, "Doubao Seed 2.0 Pro");
});

test("voice settings saves chat and book TTS settings independently", () => {
  let profiles = {
    chat: {
      voice: "",
      speedRatio: 1,
    },
    book: {
      voice: "",
      speedRatio: 1,
    },
  };
  const saved = [];
  global.wx = {
    showToast() {},
  };
  const { page } = loadVoiceSettingsPage({
    ttsExports: {
      readTTSSettingsProfiles: () => profiles,
      readScopedTTSSettings: (scope) => profiles[scope],
      saveScopedTTSSettings: (scope, settings) => {
        profiles = {
          ...profiles,
          [scope]: settings,
        };
        saved.push({ scope, settings });
        return settings;
      },
    },
  });
  const context = pageContext(page);

  page.selectTTSVoice.call(context, {
    currentTarget: {
      dataset: {
        scope: "book",
        voice: "zh_male_m191_uranus_bigtts",
      },
    },
  });
  page.selectTTSSpeed.call(context, {
    currentTarget: {
      dataset: {
        scope: "chat",
        speed: "1.5",
      },
    },
  });

  assert.deepEqual(saved, [
    {
      scope: "book",
      settings: {
        voice: "zh_male_m191_uranus_bigtts",
        speedRatio: 1,
      },
    },
    {
      scope: "chat",
      settings: {
        voice: "",
        speedRatio: 1.5,
      },
    },
  ]);
  assert.equal(context.data.bookTTSVoice, "zh_male_m191_uranus_bigtts");
  assert.equal(context.data.chatTTSSpeedRatio, 1.5);
});
