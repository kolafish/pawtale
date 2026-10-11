require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  guidanceModeLabel,
  defaultNarrationSessionFields,
  memoirProjectProfile,
  narratorGenderLabel,
  narratorProfileLine,
  projectProfileSummary,
} = require("../services/memoir-project-profile.ts");

test("normalizes memoir project profile metadata", () => {
  const project = {
    metadata_json: {
      memoir_profile: {
        narrator_gender: "female",
        narrator_origin: " 四川成都 ",
        narrator_age: 72.2,
        default_guidance_mode: "guided_interview",
        default_interview_type: "ai_interview",
        default_session_goal: "open_narration",
      },
    },
  };

  assert.deepEqual(memoirProjectProfile(project), {
    narratorName: "",
    narratorGender: "female",
    narratorOrigin: "四川成都",
    narratorAge: 72,
    defaultGuidanceMode: "guided_interview",
    defaultInterviewType: "ai_interview",
    defaultSessionGoal: "open_narration",
    defaultNarrationMode: "guided_interview",
  });
});

test("formats memoir project profile labels", () => {
  const project = {
    metadata_json: {
      memoir_profile: {
        narrator_gender: "male",
        narrator_origin: "重庆",
        narrator_age: 68,
        default_guidance_mode: "free_narration",
        default_interview_type: "human_interview",
        default_session_goal: "open_narration",
      },
    },
  };

  assert.equal(narratorGenderLabel("male"), "男");
  assert.equal(guidanceModeLabel("guided_interview"), "AI 访谈");
  assert.equal(guidanceModeLabel("free_narration"), "AI 访谈");
  assert.equal(projectProfileSummary(project), "男 · 重庆 · 68 岁 · 真人访谈");
  assert.deepEqual(defaultNarrationSessionFields("human_interview"), {
    guidanceMode: "free_narration",
    interviewType: "human_interview",
    sessionGoal: "open_narration",
  });
});

test("normalizes the book-completion project default", () => {
  const project = {
    metadata_json: {
      memoir_profile: {
        default_guidance_mode: "guided_interview",
        default_interview_type: "ai_interview",
        default_session_goal: "book_completion",
      },
    },
  };

  assert.equal(memoirProjectProfile(project).defaultNarrationMode, "book_completion");
  assert.deepEqual(defaultNarrationSessionFields("book_completion"), {
    guidanceMode: "guided_interview",
    interviewType: "ai_interview",
    sessionGoal: "book_completion",
  });
});

test("project edit validates age text", () => {
  const pagePath = require.resolve("../pages/project-edit/index.ts");
  const previousPage = global.Page;
  global.Page = () => {};
  delete require.cache[pagePath];
  const { ageFromText } = require(pagePath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }

  assert.equal(ageFromText(""), null);
  assert.equal(ageFromText("72"), 72);
  assert.throws(() => ageFromText("131"), /年龄需要填写/);
});

test("home profile line leaves out the name already in the title", () => {
  const profile = { narrator_name: "王秀英", narrator_gender: "female", narrator_age: 74, narrator_origin: "四川成都" };

  assert.equal(
    narratorProfileLine({ title: "王秀英的回忆录", metadata_json: { memoir_profile: profile } }),
    "女 · 74 岁 · 四川成都",
  );
  assert.equal(
    narratorProfileLine({ title: "妈妈的一生", metadata_json: { memoir_profile: profile } }),
    "王秀英 · 女 · 74 岁 · 四川成都",
  );
  assert.equal(narratorProfileLine({ title: "我的回忆录", metadata_json: {} }), "");
});
