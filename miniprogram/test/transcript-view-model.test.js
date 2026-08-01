require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  transcriptAudioText,
  transcriptBookInclusionText,
  transcriptDurationText,
  transcriptMaterialChapterText,
  transcriptMaterialSectionText,
} = require("../services/transcript-view-model.ts");

test("transcript view model formats duration and audio text consistently", () => {
  const item = {
    audio_duration_seconds: null,
    start_ms: 1000,
    end_ms: 10500,
  };

  assert.equal(transcriptDurationText(item), "10 秒");
  assert.equal(transcriptAudioText(item), "10 秒");
  assert.equal(transcriptAudioText(item, { includeDurationLabel: true }), "时长 10 秒");
  assert.equal(transcriptAudioText({ audio_duration_seconds: null }), "时长未知");
});

test("transcript view model derives book inclusion text", () => {
  const assigned = {
    is_included_in_book: true,
    included_chapters: [
      { chapter_id: "chapter-1", title: "退休后的日子", sort_order: 0 },
    ],
    section_assignment: {
      chapter_id: "chapter-1",
      chapter_title: "退休后的日子",
      chapter_sort_order: 0,
      section_id: "section-2",
      section_title: "退休不下岗",
      section_sort_order: 1,
      requires_user_assignment: false,
    },
  };
  assert.equal(
    transcriptBookInclusionText(assigned),
    "已放入第 1 章：退休后的日子 · 小节 2：退休不下岗",
  );
  assert.equal(transcriptMaterialChapterText(assigned), "第 1 章：退休后的日子");
  assert.equal(transcriptMaterialSectionText(assigned), "小节 2：退休不下岗");
  assert.equal(
    transcriptBookInclusionText({
      included_chapters: [
        { chapter_id: "chapter-2", title: "离家求学", sort_order: 1 },
        { chapter_id: "chapter-1", title: "童年院子", sort_order: 0 },
      ],
    }),
    "已放入第 1 章：童年院子等 2 章",
  );
  assert.equal(
    transcriptBookInclusionText({
      included_chapters: [
        {
          chapter_id: "chapter-2",
          title: "暂不成书的补记",
          sort_order: 1,
          include_in_book: false,
        },
      ],
    }),
    "已整理到第 2 章：暂不成书的补记（不收入本书）",
  );
  assert.equal(transcriptBookInclusionText({ included_chapters: [] }), "尚未入章");
  assert.equal(
    transcriptMaterialSectionText({
      section_assignment: {
        ...assigned.section_assignment,
        requires_user_assignment: true,
      },
    }),
    "待归类素材（请选择小节）",
  );
});
