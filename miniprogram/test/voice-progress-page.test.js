require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  loadPageModule,
  mockMemoirApiModules,
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

function sampleSession(id, status) {
  return {
    id,
    project_id: "project-1",
    status,
    guidance_mode: "guided_interview",
    current_guidance_module: "person",
    started_at: null,
    stopped_at: null,
    created_at: "2026-06-18T00:00:00.000Z",
    updated_at: "2026-06-18T00:00:00.000Z",
    user_message_count: 1,
    assistant_message_count: 1,
    utterance_count: 1,
    total_user_char_count: 12,
    total_audio_seconds: 8,
    last_message_text: "我小时候住在老家。",
    last_message_role: "user",
    last_message_at: "2026-06-18T00:00:00.000Z",
  };
}

function defaultIdentity() {
  return {
    id: "user-1",
    email: "memoir-author@example.com",
    password_login_enabled: true,
    display_name: "作者",
    avatar_url: "",
    created_at: "2026-06-18T00:00:00.000Z",
    active_project_id: "project-1",
    active_project: {
      id: "project-1",
      title: "爷爷回忆录",
      metadata_json: {},
    },
    projects: [],
  };
}

function loadOverviewPage({
  authExports = {},
  topicApiExports = {},
  voiceApiExports = {},
  authorizationExports = {},
} = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => defaultIdentity(),
    ...authExports,
  });
  mockMemoirApiModules({
    getMemoirVoiceProgress: async () => ({
      session_count: 0,
      active_session_count: 0,
      stopped_session_count: 0,
      failed_session_count: 0,
      utterance_count: 0,
      user_message_count: 0,
      assistant_message_count: 0,
      total_user_char_count: 0,
      total_audio_seconds: 0,
      modules: [],
    }),
    listMemoirVoiceSessions: async () => ({ items: [], total: 0, limit: 30, offset: 0 }),
    createMemoirVoiceSession: unmockedAsync("createMemoirVoiceSession"),
    ...voiceApiExports,
  });
  mockModule("../services/memoir-topic-map-api.ts", {
    getMemoirTopicMap: async () => topicMapFixture(),
    updateMemoirTopicMapResolution: unmockedAsync("updateMemoirTopicMapResolution"),
    ...topicApiExports,
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

  return loadPageModule("../pages/voice-progress/index.ts");
}

test("narration overview is the only registered topic and progress page", () => {
  const appJson = JSON.parse(
    fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"),
  );
  assert.ok(appJson.pages.includes("pages/voice-progress/index"));
  assert.ok(!appJson.pages.includes("pages/topic-map/index"));
});

test("narration overview maps session status by backend status", async () => {
  const { page } = loadOverviewPage({
    voiceApiExports: {
      getMemoirVoiceProgress: async () => ({
        session_count: 3,
        active_session_count: 1,
        stopped_session_count: 1,
        failed_session_count: 1,
        utterance_count: 3,
        user_message_count: 3,
        assistant_message_count: 3,
        total_user_char_count: 36,
        total_audio_seconds: 24,
        modules: [],
      }),
      listMemoirVoiceSessions: async () => ({
        items: [
          sampleSession("voice-listening", "listening"),
          sampleSession("voice-stopped", "stopped"),
          sampleSession("voice-failed", "failed"),
        ],
        total: 3,
        limit: 30,
        offset: 0,
      }),
    },
  });
  const context = {
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadOverview.call(context);

  assert.equal(context.data.sessions[0].canContinue, true);
  assert.equal(context.data.sessions[1].canContinue, false);
  assert.equal(context.data.sessions[2].canContinue, false);
  assert.equal("canViewTranscripts" in context.data.sessions[1], false);
  assert.equal("actionText" in context.data.sessions[1], false);
});

test("narration overview scopes all data to the active project", async () => {
  const progressProjectIds = [];
  const sessionOptions = [];
  const topicProjectIds = [];
  const { page } = loadOverviewPage({
    authExports: {
      getMemoirIdentity: async () => ({
        ...defaultIdentity(),
        active_project_id: "project-current",
        active_project: {
          id: "project-current",
          title: "外婆回忆录",
          metadata_json: {},
        },
        projects: [
          { id: "project-current", title: "外婆回忆录" },
          { id: "project-other", title: "爷爷回忆录" },
        ],
      }),
    },
    voiceApiExports: {
      getMemoirVoiceProgress: async (projectId) => {
        progressProjectIds.push(projectId);
        return {
          session_count: 1,
          active_session_count: 1,
          stopped_session_count: 0,
          failed_session_count: 0,
          utterance_count: 2,
          user_message_count: 2,
          assistant_message_count: 1,
          total_user_char_count: 80,
          total_audio_seconds: 30,
          modules: [],
        };
      },
      listMemoirVoiceSessions: async (options) => {
        sessionOptions.push(options);
        return { items: [sampleSession("voice-current", "listening")], total: 1, limit: 30, offset: 0 };
      },
    },
    topicApiExports: {
      getMemoirTopicMap: async (projectId) => {
        topicProjectIds.push(projectId);
        return topicMapFixture();
      },
    },
  });
  const context = {
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadOverview.call(context);

  assert.deepEqual(progressProjectIds, ["project-current"]);
  assert.deepEqual(sessionOptions, [{ projectId: "project-current", effectiveOnly: true, limit: 30 }]);
  assert.deepEqual(topicProjectIds, ["project-current"]);
  assert.equal(context.data.activeProjectId, "project-current");
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.progress.session_count, 1);
  assert.equal(context.data.sessions.length, 1);
});

test("narration overview loads no project data without an active project", async () => {
  let progressRequested = false;
  let sessionsRequested = false;
  let topicsRequested = false;
  const { page } = loadOverviewPage({
    authExports: {
      getMemoirIdentity: async () => ({
        ...defaultIdentity(),
        active_project_id: "",
        active_project: null,
        projects: [],
      }),
    },
    voiceApiExports: {
      getMemoirVoiceProgress: async () => {
        progressRequested = true;
        return {};
      },
      listMemoirVoiceSessions: async () => {
        sessionsRequested = true;
        return { items: [], total: 0, limit: 30, offset: 0 };
      },
    },
    topicApiExports: {
      getMemoirTopicMap: async () => {
        topicsRequested = true;
        return topicMapFixture();
      },
    },
  });
  const context = {
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadOverview.call(context);

  assert.equal(progressRequested, false);
  assert.equal(sessionsRequested, false);
  assert.equal(topicsRequested, false);
  assert.equal(context.data.progress.session_count, 0);
  assert.deepEqual(context.data.sessions, []);
});

test("narration overview only resumes active sessions", () => {
  const { page } = loadOverviewPage();
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
      sessions: [
        {
          ...sampleSession("voice-listening", "listening"),
          canContinue: true,
        },
        {
          ...sampleSession("voice-stopped", "stopped"),
          canContinue: false,
        },
      ],
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.continueSession.call(context, { currentTarget: { dataset: { id: "voice-stopped" } } });
  page.continueSession.call(context, { currentTarget: { dataset: { id: "voice-listening" } } });

  assert.equal(context.data.error, "这次讲述已保存");
  assert.equal(navigations[0], "/pages/voice-chat/index?voiceSessionId=voice-listening&mode=guided_interview&module=person");
});

test("narration overview keeps evidence and user decisions as separate topic states", () => {
  const {
    topicMapResolutionChoices,
    topicMapViewPatch,
  } = loadOverviewPage().mod;
  const patch = topicMapViewPatch(topicMapFixture());

  assert.equal(patch.topicModules[0].items[0].state_text, "已经聊过");
  assert.equal(patch.topicModules[0].items[0].actionText, "调整这个话题");
  assert.equal(patch.topicModules[0].items[0].startDisabled, false);
  assert.equal(patch.topicModules[0].items[1].state_text, "不想聊这个");
  assert.equal(patch.topicModules[0].items[1].actionText, "修改我的选择");
  assert.equal(patch.topicModules[0].items[1].startDisabled, true);
  assert.equal(topicMapResolutionChoices("do_not_discuss")[0].value, "none");
  assert.doesNotMatch(
    JSON.stringify(patch),
    /coverage|sufficient|FrameworkItem|Claim|Gap|Task/,
  );
});

test("narration overview starts an explicitly anchored guided session", async () => {
  const created = [];
  const { page, mod } = loadOverviewPage({
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
      activeProjectId: "project-1",
      topicModules: mod.topicMapViewPatch(topicMapFixture()).topicModules,
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
  assert.equal(created[0].input.metadata_json.entry, "miniprogram_voice_overview");
  assert.equal(created[0].options.projectId, "project-1");
  assert.match(navigations[0], /voiceSessionId=voice-1/);
  assert.match(navigations[0], /mode=guided_interview/);
});

test("narration overview waits for voice authorization before starting a topic", async () => {
  let created = false;
  const { page, mod } = loadOverviewPage({
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
      activeProjectId: "project-1",
      topicModules: mod.topicMapViewPatch(topicMapFixture()).topicModules,
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

test("narration overview updates only the selected topic resolution", async () => {
  const updated = {
    ...topicMapFixture().modules[0].items[0],
    user_resolution: "enough_for_now",
    display_state: "enough_for_now",
    state_text: "目前讲这些就够了",
    state_detail: "这是您的选择，以后可以随时撤销。",
    resolved_at: "2026-07-16T00:00:00",
  };
  const { page, mod } = loadOverviewPage({
    topicApiExports: {
      updateMemoirTopicMapResolution: async (projectId, itemId, resolution) => {
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
      activeProjectId: "project-1",
      topicModules: mod.topicMapViewPatch(topicMapFixture()).topicModules,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.saveResolution.call(context, "item-1", "enough_for_now");

  assert.equal(context.data.topicModules[0].items[0].state_text, "目前讲这些就够了");
  assert.equal(context.data.topicModules[0].items[1].state_text, "不想聊这个");
  assert.equal(toasts[0].title, "已保存");
});
