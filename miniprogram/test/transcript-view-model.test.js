require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  transcriptAudioText,
  transcriptBookInclusionText,
  transcriptDurationText,
  transcriptVoiceEditPreviewView,
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
  assert.equal(
    transcriptBookInclusionText({
      included_chapters: [
        { chapter_id: "chapter-2", title: "离家求学", sort_order: 1 },
        { chapter_id: "chapter-1", title: "童年院子", sort_order: 0 },
      ],
    }),
    "已放入第 1 章：童年院子等 2 章",
  );
  assert.equal(transcriptBookInclusionText({ included_chapters: [] }), "尚未入章");
});

test("transcript view model builds voice edit preview diff", () => {
  const preview = transcriptVoiceEditPreviewView({
    action: "revise_transcript",
    current_text: "他叫李月，我们是高中同学。",
    edited_text: "他叫李阅，我们是高中同学。",
    reply_text: "",
    confidence: 0.93,
    changes: [{ before: "李月", after: "李阅", reason: "用户明确纠正名字" }],
  });

  assert.equal(preview.confidenceText, "93%");
  assert.equal(preview.canConfirm, true);
  assert.deepEqual(preview.diffSegments, [
    { text: "他叫", kind: "equal" },
    { text: "李月", kind: "delete" },
    { text: "李阅", kind: "insert" },
    { text: "，我们是高中同学。", kind: "equal" },
  ]);
});
