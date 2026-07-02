require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  guidanceModeLabel,
  memoirProjectProfile,
  narratorGenderLabel,
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
      },
    },
  };

  assert.deepEqual(memoirProjectProfile(project), {
    narratorName: "",
    narratorGender: "female",
    narratorOrigin: "四川成都",
    narratorAge: 72,
    defaultGuidanceMode: "guided_interview",
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
      },
    },
  };

  assert.equal(narratorGenderLabel("male"), "男");
  assert.equal(guidanceModeLabel("guided_interview"), "小余陪着问");
  assert.equal(projectProfileSummary(project), "男 · 重庆 · 68 岁 · 慢慢讲");
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
