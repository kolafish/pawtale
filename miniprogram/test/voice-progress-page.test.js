require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

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

function loadProgressPage({ authExports = {}, voiceApiExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const progressPath = require.resolve("../pages/voice-progress/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[progressPath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      getMemoirIdentity: async () => defaultIdentity(),
      ...authExports,
    },
  };
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
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
      ...voiceApiExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  const mod = require(progressPath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return { mod, page };
}

test("voice progress maps session status by backend status", async () => {
  const { page } = loadProgressPage({
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

  await page.loadProgress.call(context);

  assert.equal(context.data.sessions[0].canContinue, true);
  assert.equal(context.data.sessions[1].canContinue, false);
  assert.equal(context.data.sessions[2].canContinue, false);
  assert.equal("canViewTranscripts" in context.data.sessions[1], false);
  assert.equal("actionText" in context.data.sessions[1], false);
});

test("voice progress scopes progress and sessions to the active project", async () => {
  const progressProjectIds = [];
  const sessionOptions = [];
  const { page } = loadProgressPage({
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
  });
  const context = {
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadProgress.call(context);

  assert.deepEqual(progressProjectIds, ["project-current"]);
  assert.deepEqual(sessionOptions, [{ projectId: "project-current", effectiveOnly: true, limit: 30 }]);
  assert.equal(context.data.activeProjectId, "project-current");
  assert.equal(context.data.projectTitle, "外婆回忆录");
  assert.equal(context.data.progress.session_count, 1);
  assert.equal(context.data.sessions.length, 1);
});

test("voice progress does not load all-project stats without an active project", async () => {
  let progressRequested = false;
  let sessionsRequested = false;
  const { page } = loadProgressPage({
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
  });
  const context = {
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.loadProgress.call(context);

  assert.equal(progressRequested, false);
  assert.equal(sessionsRequested, false);
  assert.equal(context.data.progress.session_count, 0);
  assert.deepEqual(context.data.sessions, []);
});

test("voice progress only resumes active sessions", () => {
  const { page } = loadProgressPage();
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
  delete global.wx;
});
