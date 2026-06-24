require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  assistantPanelCopy,
  assistantTranscriptState,
  canRequestFollowUp,
  countTextChars,
  coveredFollowUpSourceMessageId,
  followUpSourceMessageId,
  latestAssistantMessageId,
  latestTranscriptText,
  totalTranscriptChars,
  ttsAlignmentFromMetadata,
  upsertTranscriptLine,
  visibleAssistantLine,
} = require("../services/voice-chat-state.ts");

const userLine = {
  id: "user-line",
  role: "user",
  text: " 我  想聊外婆 ",
  message_id: "message-user",
  utterance_id: "utterance-user",
};

const assistantLine = {
  id: "assistant-line",
  role: "assistant",
  text: "那时候你最记得谁？",
  message_id: "message-assistant",
  metadata_json: {
    tts_timing_source: "volc_tts",
    tts_alignment: [
      {
        text: "那",
        start_ms: 0,
        end_ms: 120,
        text_start: 0,
        text_end: 1,
        confidence: 0.9,
      },
      {
        text: "",
        start_ms: 120,
        end_ms: 100,
        text_start: 1,
        text_end: 2,
      },
    ],
  },
};

test("derives assistant panel state from transcript lines", () => {
  const lines = [userLine, assistantLine];

  assert.equal(latestTranscriptText(lines), userLine.text);
  assert.equal(followUpSourceMessageId(lines), "message-user");
  assert.equal(coveredFollowUpSourceMessageId(lines), "message-user");
  assert.equal(latestAssistantMessageId(lines), "message-assistant");
  assert.equal(visibleAssistantLine(lines, ""), assistantLine);
  assert.equal(visibleAssistantLine(lines, "message-assistant"), undefined);
  assert.deepEqual(assistantPanelCopy(lines), {
    assistantPromptLabel: "AI 追问",
    assistantLoadingText: "正在根据刚才的内容生成下一句追问",
    assistantEmptyText: "可以让 AI 根据刚才的内容继续追问。",
  });
});

test("derives assistant transcript patch from lines and overrides generated assistant fields", () => {
  assert.deepEqual(
    assistantTranscriptState([userLine, assistantLine], {
      guidanceMode: "guided_interview",
      lastFollowUpSourceMessageId: "message-user",
      followUpLoading: false,
      currentFollowUpTTSStateText: "等待语音",
    }),
    {
      transcriptLines: [userLine, assistantLine],
      latestTranscriptText: userLine.text,
      assistantPromptText: assistantLine.text,
      assistantPromptLabel: "AI 追问",
      assistantLoadingText: "正在根据刚才的内容生成下一句追问",
      assistantEmptyText: "可以让 AI 根据刚才的内容继续追问。",
      followUpMessageId: "message-assistant",
      followUpAudioFileId: "",
      followUpTTSStateText: "等待语音",
      ttsAlignment: [
        {
          text: "那",
          start_ms: 0,
          end_ms: 120,
          text_start: 0,
          text_end: 1,
          confidence: 0.9,
        },
      ],
      totalTranscriptCharCount: 5,
      canRequestFollowUp: false,
    },
  );

  assert.equal(
    assistantTranscriptState([userLine, assistantLine], {
      guidanceMode: "guided_interview",
      lastFollowUpSourceMessageId: "",
      followUpLoading: true,
      assistantPromptText: "覆盖后的追问",
      followUpMessageId: "message-override",
      followUpAudioFileId: "audio-override",
      followUpTTSStateText: "",
      ttsAlignment: [],
      canRequestFollowUp: false,
    }).assistantPromptText,
    "覆盖后的追问",
  );
});

test("derives opening follow-up source before the first user line", () => {
  assert.equal(followUpSourceMessageId([]), "opening");
  assert.deepEqual(assistantPanelCopy([]), {
    assistantPromptLabel: "AI 开场",
    assistantLoadingText: "正在准备第一句话",
    assistantEmptyText: "AI 会先说一句，帮你开始讲。",
  });
  assert.equal(canRequestFollowUp([], "free_narration", "", false), false);
  assert.equal(canRequestFollowUp([], "guided_interview", "opening", false), false);
  assert.equal(canRequestFollowUp([], "guided_interview", "", true), false);
  assert.equal(canRequestFollowUp([], "guided_interview", "", false), true);
});

test("parses valid TTS alignment metadata and drops invalid segments", () => {
  assert.deepEqual(ttsAlignmentFromMetadata(assistantLine.metadata_json), [
    {
      text: "那",
      start_ms: 0,
      end_ms: 120,
      text_start: 0,
      text_end: 1,
      confidence: 0.9,
    },
  ]);
  assert.deepEqual(ttsAlignmentFromMetadata({ tts_alignment: "bad" }), []);
});

test("upserts transcript lines by message or utterance identity", () => {
  const updatedByMessage = upsertTranscriptLine([userLine], {
    ...userLine,
    text: "我想聊外婆",
  });
  assert.deepEqual(updatedByMessage, [{ ...userLine, text: "我想聊外婆" }]);

  const appended = upsertTranscriptLine(updatedByMessage, assistantLine);
  assert.deepEqual(appended, [{ ...userLine, text: "我想聊外婆" }, assistantLine]);
});

test("counts transcript characters from user lines only", () => {
  assert.equal(countTextChars(" 我 想聊 外婆 "), 5);
  assert.equal(totalTranscriptChars([userLine, assistantLine]), 5);
});
