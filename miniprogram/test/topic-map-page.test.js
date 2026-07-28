require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  unmockedAsync,
} = require("./page-harness");

function topicMapFixture() {
  return {
    project_id: "project-1",
    template_key: "memoir_life_topics",
    template_version: "1",
    headline: "这些是聊过的人生话题",
    detail: "它不是章节目录，也没有必须全部讲完。",
    modules: [
      {
        module_key: "life_stage",
        title: "人生阶段",
        items: [
          {
            id: "item-1",
            item_key: "childhood_and_growth",
            title: "童年与成长",
            intent: "童年生活、家庭环境和成长经历",
            evidence_state: "has_material",
            user_resolution: "none",
            display_state: "already_shared",
            state_text: "已经聊过",
            state_detail: "这里已经有相关讲述。",
            resolved_at: null,
          },
          {
            id: "item-2",
            item_key: "school_and_learning",
            title: "求学与学本事",
            intent: "上学、求学和学习带来的改变",
            evidence_state: "none",
            user_resolution: "do_not_discuss",
            display_state: "do_not_discuss",
            state_text: "不想聊这个",
            state_detail: "这个方向会被收起。",
            resolved_at: "2026-07-16T00:00:00",
          },
        ],
      },
    ],
  };
}

function loadTopicMapPage({
  apiExports = {},
  voiceApiExports = {},
  authorizationExports = {},
} = {}) {
  mockModule("../services/memoir-topic-map-api.ts", {
    getMemoirTopicMap: async () => topicMapFixture(),
    updateMemoirTopicMapResolution: unmockedAsync("updateMemoirTopicMapResolution"),
    ...apiExports,
  });
  mockModule("../services/accessibility-settings.ts", {
    readLargeTextEnabled: () => false,
  });
  mockModule("../services/memoir-voice-session-api.ts", {
    createMemoirVoiceSession: unmockedAsync("createMemoirVoiceSession"),
    ...voiceApiExports,
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => true,
    syncVoiceAuthorizationConsent: async () => ({}),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
    ...authorizationExports,
  });
  mockModule("../services/navigation.ts", {
    routeAuthError: () => false,
  });
  return loadPageModule("../pages/topic-map/index.ts");
}

test("topic map page is registered", () => {
  const appJson = JSON.parse(
    fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"),
  );
  assert.ok(appJson.pages.includes("pages/topic-map/index"));
});

test("topic map view keeps evidence and user decisions as separate states", () => {
  const {
    topicMapResolutionChoices,
    topicMapViewPatch,
  } = loadTopicMapPage().mod;
  const patch = topicMapViewPatch(topicMapFixture());

  assert.equal(patch.modules[0].items[0].state_text, "已经聊过");
  assert.equal(patch.modules[0].items[0].actionText, "调整这个话题");
  assert.equal(patch.modules[0].items[0].startDisabled, false);
  assert.equal(patch.modules[0].items[1].state_text, "不想聊这个");
  assert.equal(patch.modules[0].items[1].actionText, "修改我的选择");
  assert.equal(patch.modules[0].items[1].startDisabled, true);
  assert.equal(
    topicMapResolutionChoices("do_not_discuss")[0].value,
    "none",
  );
  assert.doesNotMatch(
    JSON.stringify(patch),
    /coverage|sufficient|FrameworkItem|Claim|Gap|Task/,
  );
});

test("starting from a topic creates an explicitly anchored guided session", async () => {
  const created = [];
  const { page, mod } = loadTopicMapPage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input, options) => {
        created.push({ input, options });
        return {
          session: { id: "voice-1" },
          messages: [],
          utterances: [],
        };
      },
    },
  });
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const context = {
    ...page,
    data: {
      ...page.data,
      projectId: "project-1",
      modules: mod.topicMapViewPatch(topicMapFixture()).modules,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startFromTopic.call(context, {
    currentTarget: { dataset: { itemId: "item-1" } },
  });

  assert.equal(created.length, 1);
  assert.equal(created[0].input.guidance_mode, "guided_interview");
  assert.equal(created[0].input.session_goal, "open_narration");
  assert.equal(created[0].input.anchor_framework_item_id, "item-1");
  assert.equal(created[0].options.projectId, "project-1");
  assert.match(navigations[0], /voiceSessionId=voice-1/);
  assert.match(navigations[0], /mode=guided_interview/);
});

test("starting from a topic waits for voice authorization", async () => {
  let created = false;
  const { page, mod } = loadTopicMapPage({
    voiceApiExports: {
      createMemoirVoiceSession: async () => {
        created = true;
      },
    },
    authorizationExports: {
      readVoiceAuthorizationConsent: () => false,
    },
  });
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const context = {
    ...page,
    data: {
      ...page.data,
      projectId: "project-1",
      modules: mod.topicMapViewPatch(topicMapFixture()).modules,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startFromTopic.call(context, {
    currentTarget: { dataset: { itemId: "item-1" } },
  });

  assert.equal(created, false);
  assert.deepEqual(navigations, ["/pages/voice-authorization/index"]);
});

test("saving a resolution updates only the selected topic", async () => {
  const updated = {
    ...topicMapFixture().modules[0].items[0],
    user_resolution: "enough_for_now",
    display_state: "enough_for_now",
    state_text: "目前讲这些就够了",
    state_detail: "这是您的选择，以后可以随时撤销。",
    resolved_at: "2026-07-16T00:00:00",
  };
  const { page, mod } = loadTopicMapPage({
    apiExports: {
      updateMemoirTopicMapResolution: async (
        projectId,
        itemId,
        resolution,
      ) => {
        assert.equal(projectId, "project-1");
        assert.equal(itemId, "item-1");
        assert.equal(resolution, "enough_for_now");
        return updated;
      },
    },
  });
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const context = {
    ...page,
    data: {
      ...page.data,
      projectId: "project-1",
      modules: mod.topicMapViewPatch(topicMapFixture()).modules,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.saveResolution.call(context, "item-1", "enough_for_now");

  assert.equal(context.data.modules[0].items[0].state_text, "目前讲这些就够了");
  assert.equal(context.data.modules[0].items[1].state_text, "不想聊这个");
  assert.equal(toasts[0].title, "已保存");
});
