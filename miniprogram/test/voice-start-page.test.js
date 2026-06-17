require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadVoiceStartPage({ authExports = {}, voiceApiExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const pagePath = require.resolve("../pages/voice-start/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[pagePath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      getMemoirIdentity: async () => {
        throw new Error("getMemoirIdentity was not mocked");
      },
      ...authExports,
    },
  };
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
      createMemoirVoiceSession: async () => {
        throw new Error("createMemoirVoiceSession was not mocked");
      },
      ...voiceApiExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  require(pagePath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return page;
}

function pageContext(data) {
  return {
    data: { ...data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };
}

test("voice start refreshes guidance mode from current project on show", async () => {
  const page = loadVoiceStartPage({
    authExports: {
      getMemoirIdentity: async () => ({
        active_project_id: "project-1",
        active_project: {
          title: "外婆回忆录",
          metadata_json: {
            memoir_profile: {
              default_guidance_mode: "guided_interview",
            },
          },
        },
      }),
    },
  });
  const context = pageContext(page.data);

  await page.onShow.call(context);

  assert.equal(context.data.selectedMode, "guided_interview");
  assert.equal(context.data.guidanceLabel, "主题讲述");
  assert.equal(context.data.projectTitle, "外婆回忆录");
});

test("voice start creates sessions with the project-bound guidance mode", async () => {
  const createdSessions = [];
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const page = loadVoiceStartPage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input) => {
        createdSessions.push(input);
        return {
          session: {
            id: "voice/1",
          },
        };
      },
    },
  });
  const context = pageContext({
    ...page.data,
    selectedMode: "guided_interview",
    selectedModule: "event",
  });

  await page.startVoiceChat.call(context);

  assert.equal(createdSessions.length, 1);
  assert.equal(createdSessions[0].guidance_mode, "guided_interview");
  assert.equal(createdSessions[0].current_guidance_module, "event");
  assert.equal(navigations[0], "/pages/voice-chat/index?voiceSessionId=voice%2F1&mode=guided_interview&module=event");
  delete global.wx;
});
