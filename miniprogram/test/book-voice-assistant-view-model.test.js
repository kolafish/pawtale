require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  bookVoiceAssistantReplyText,
  bookVoiceCommandParseRequest,
  bookVoiceCommandPreviewPatch,
  isBookVoiceCommandConfirmationText,
} = require("../pages/book/book-voice-assistant-view-model.ts");

function samplePreview(overrides = {}) {
  return {
    original_text: "把第一章改名为童年往事",
    intent: "rename_chapter",
    intent_label: "修改章名",
    response_kind: "action_preview",
    summary: "准备把「外婆家的童年」改名为「童年往事」。",
    reply_text: null,
    operations: [
      {
        type: "rename_chapter",
        chapter_id: "chapter-1",
        chapter_title: "外婆家的童年",
        new_title: "童年往事",
      },
    ],
    requires_confirmation: true,
    used_ai: true,
    provider: "fake",
    model: "fake-model",
    ...overrides,
  };
}

test("book voice command preview patch derives panel copy and executable state", () => {
  assert.deepEqual(bookVoiceCommandPreviewPatch(samplePreview()), {
    bookVoiceCommandText: "把第一章改名为童年往事",
    bookVoiceCommandIntentLabel: "修改章名",
    bookVoiceCommandResponseKind: "action_preview",
    bookVoiceCommandPanelTitle: "操作预览",
    bookVoiceCommandStateText: "待确认",
    bookVoiceCommandDetailText: "准备把「外婆家的童年」改名为「童年往事」。",
    bookVoiceCommandSummary: "准备把「外婆家的童年」改名为「童年往事」。",
    bookVoiceCommandRequiresConfirmation: true,
    bookVoiceCommandPreview: samplePreview(),
    bookVoiceCommandCanExecute: true,
    bookVoiceCommandActionText: "确认执行",
    bookVoiceCommandUndoText: "",
    bookVoiceCommandUndoSnapshot: null,
    bookVoiceAssistantStatusText: "已理解，等待确认",
    error: "",
  });
});

test("book voice parse request sends prior conversation and capped source coverage", () => {
  const request = bookVoiceCommandParseRequest(
    "现在有几个章节啊",
    [
      { id: "user-1", role: "user", text: " 我可以修改些啥 " },
      { id: "assistant-1", role: "assistant", text: "可以改章名、调顺序、合并章节。" },
      { id: "user-2", role: "user", text: "现在有几个章节啊" },
    ],
    {
      total_source_count: 3,
      covered_source_count: 1,
      uncovered_source_count: 2,
      covered_sources: [],
      uncovered_sources: [
        { source_index: 2, utterance_id: "utterance-2", text: "第一条新增转写" },
        { source_index: 3, utterance_id: "utterance-3", text: "第二条新增转写" },
      ],
    },
  );

  assert.deepEqual(request, {
    text: "现在有几个章节啊",
    conversation_context: [
      { role: "user", text: "我可以修改些啥" },
      { role: "assistant", text: "可以改章名、调顺序、合并章节。" },
    ],
    book_context: {
      source_coverage: {
        total_source_count: 3,
        covered_source_count: 1,
        uncovered_source_count: 2,
      },
      uncovered_sources: [
        { source_index: 2, utterance_id: "utterance-2", text: "第一条新增转写" },
        { source_index: 3, utterance_id: "utterance-3", text: "第二条新增转写" },
      ],
    },
  });
});

test("book voice reply and confirmation helpers keep command flow copy stable", () => {
  assert.equal(
    bookVoiceAssistantReplyText(samplePreview()),
    "我理解了，准备把「外婆家的童年」改名为「童年往事」。 如果没问题，说确认执行，我再改。",
  );
  assert.equal(isBookVoiceCommandConfirmationText(" 确认执行。"), true);
  assert.equal(isBookVoiceCommandConfirmationText("先不要改"), false);
});
