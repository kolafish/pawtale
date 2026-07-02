require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  sourceAssignmentRequest,
  transcriptChapterAssignOptions,
  transcriptChapterMoveOptions,
  transcriptSourceMoveRequest,
} = require("../services/memoir-book-source-actions.ts");

function sourceRef(id) {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 8000,
    created_at: "2026-06-17T00:00:00.000Z",
    quote: "往事",
  };
}

function chapter(id, title, sortOrder, sourceRefs) {
  return {
    id,
    plan_id: "plan-1",
    project_id: "project-1",
    title,
    sort_order: sortOrder,
    source_refs: sourceRefs,
    metadata_json: {},
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
  };
}

function plan() {
  return {
    id: "plan-1",
    project_id: "project-1",
    title: "回忆录",
    status: "confirmed",
    created_by: "ai",
    metadata_json: {},
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
    chapters: [
      chapter("chapter-1", "岁首欢聚记", 0, [sourceRef("utterance-1"), sourceRef("utterance-2")]),
      chapter("chapter-2", "童年友情", 1, [sourceRef("utterance-3")]),
    ],
  };
}

function transcript(id, included = false) {
  return {
    id,
    voice_session_id: "voice-1",
    project_id: "project-1",
    project_title: "回忆录",
    question_text: "",
    transcript_raw: "原文",
    transcript_edited: "",
    display_text: "原文",
    raw_char_count: 2,
    edited_char_count: 0,
    display_char_count: 2,
    session_status: "completed",
    guidance_mode: "guided_interview",
    is_included_in_book: included,
    included_chapters: included ? [{ chapter_id: "chapter-1", title: "岁首欢聚记", sort_order: 0 }] : [],
    created_at: "2026-06-17T00:00:00.000Z",
    session_created_at: "2026-06-17T00:00:00.000Z",
  };
}

test("sourceAssignmentRequest builds a single-source chapter assignment payload", () => {
  assert.deepEqual(
    sourceAssignmentRequest("chapter-1", "utterance-new"),
    { assignments: [{ chapter_id: "chapter-1", utterance_ids: ["utterance-new"] }] },
  );
});

test("transcriptChapterAssignOptions lists chapters only for uncovered transcripts", () => {
  assert.deepEqual(
    transcriptChapterAssignOptions(transcript("utterance-new"), plan()),
    [
      { label: "第 1 章 岁首欢聚记", value: "chapter-1" },
      { label: "第 2 章 童年友情", value: "chapter-2" },
    ],
  );
  assert.deepEqual(transcriptChapterAssignOptions(transcript("utterance-2", true), plan()), []);
});

test("transcript source move helpers build requests and enforce source chapter boundaries", () => {
  const currentPlan = plan();
  assert.deepEqual(
    transcriptChapterMoveOptions(transcript("utterance-2", true), currentPlan),
    [{ label: "第 2 章 童年友情", value: "chapter-2" }],
  );
  assert.deepEqual(
    transcriptSourceMoveRequest(transcript("utterance-2", true), currentPlan, "chapter-2"),
    {
      request: {
        source_chapter_id: "chapter-1",
        target_chapter_id: "chapter-2",
        utterance_id: "utterance-2",
      },
      error: "",
    },
  );
  assert.deepEqual(transcriptChapterMoveOptions(transcript("utterance-3", true), currentPlan), []);
  assert.deepEqual(
    transcriptSourceMoveRequest(transcript("utterance-3", true), currentPlan, "chapter-1"),
    { request: null, error: "本章只剩 1 条素材；请在书稿页合并或删除章节来保留素材" },
  );
  assert.deepEqual(
    transcriptSourceMoveRequest(transcript("utterance-2", true), currentPlan, "missing-chapter"),
    { request: null, error: "请选择要移动到的章节" },
  );
});
