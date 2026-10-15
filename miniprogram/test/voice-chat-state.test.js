require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  assistantExpectsAnswer,
  assistantPanelCopy,
  assistantTranscriptState,
  canRequestFollowUp,
  countTextChars,
  coveredFollowUpSourceMessageId,
  followUpSourceMessageId,
  latestAssistantMessageId,
  latestPersistedAssistantMessageId,
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
    primary_intent: "follow_up",
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
  assert.equal(latestPersistedAssistantMessageId(lines), "message-assistant");
  assert.equal(visibleAssistantLine(lines, ""), assistantLine);
  assert.equal(visibleAssistantLine(lines, "message-assistant"), undefined);
  assert.equal(assistantExpectsAnswer(assistantLine), true);
  assert.deepEqual(assistantPanelCopy(lines), {
    assistantLoadingText: "小余正在想一想",
    assistantEmptyText: "可以让小余根据刚才的内容接着问。",
  });
});

test("only exposes persisted assistant messages as recording parents", () => {
  const streamingLine = {
    id: "assistant-stream-1",
    role: "assistant",
    text: "正在生成的问题",
    message_id: "",
  };

  assert.equal(latestPersistedAssistantMessageId([userLine, streamingLine]), "");
  assert.equal(latestPersistedAssistantMessageId([userLine, assistantLine]), "message-assistant");
  assert.equal(latestPersistedAssistantMessageId([assistantLine, userLine]), "");
});

test("does not mark a source covered by a non-answer acknowledgement", () => {
  const acknowledgementLine = {
    ...assistantLine,
    text: "您刚才说的院子，我听到了。",
    metadata_json: {
      ...assistantLine.metadata_json,
      primary_intent: "acknowledge",
      expects_answer: false,
    },
  };

  assert.equal(coveredFollowUpSourceMessageId([userLine, acknowledgementLine]), "");
});

test("derives assistant transcript patch from lines and overrides generated assistant fields", () => {
  assert.deepEqual(
    assistantTranscriptState([userLine, assistantLine], {
      guidanceMode: "guided_interview",
      interviewType: "ai_interview",
      sessionGoal: "open_narration",
      lastFollowUpSourceMessageId: "message-user",
      followUpLoading: false,
      currentFollowUpTTSStateText: "等待语音",
    }),
    {
      transcriptLines: [userLine, assistantLine],
      latestTranscriptText: userLine.text,
      assistantPromptText: assistantLine.text,
      assistantLoadingText: "小余正在想一想",
      assistantEmptyText: "可以让小余根据刚才的内容接着问。",
      assistantExpectsAnswer: true,
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
      interviewType: "ai_interview",
      sessionGoal: "open_narration",
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
  assert.equal(
    assistantTranscriptState([userLine, {
      ...assistantLine,
      metadata_json: { ...assistantLine.metadata_json, expects_answer: false },
    }], {
      guidanceMode: "guided_interview",
      interviewType: "ai_interview",
      sessionGoal: "open_narration",
      lastFollowUpSourceMessageId: "message-user",
      followUpLoading: false,
    }).assistantExpectsAnswer,
    false,
  );
});

test("derives opening follow-up source before the first user line", () => {
  assert.equal(followUpSourceMessageId([]), "opening");
  assert.deepEqual(assistantPanelCopy([]), {
    assistantLoadingText: "小余正在准备第一句话",
    assistantEmptyText: "小余会先问一句，帮您开始讲。",
  });
  assert.equal(canRequestFollowUp([], { guidanceMode: "free_narration", interviewType: "ai_interview", sessionGoal: "open_narration" }, "", false), true);
  assert.equal(canRequestFollowUp([], { guidanceMode: "guided_interview", interviewType: "ai_interview", sessionGoal: "open_narration" }, "opening", false), false);
  assert.equal(canRequestFollowUp([], { guidanceMode: "guided_interview", interviewType: "ai_interview", sessionGoal: "open_narration" }, "", true), false);
  assert.equal(canRequestFollowUp([], { guidanceMode: "guided_interview", interviewType: "ai_interview", sessionGoal: "open_narration" }, "", false), true);
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

  const updatedByTemporaryId = upsertTranscriptLine(appended, {
    id: "assistant-stream-1",
    role: "assistant",
    text: "欢迎回来",
  });
  assert.deepEqual(updatedByTemporaryId, [
    { ...userLine, text: "我想聊外婆" },
    assistantLine,
    { id: "assistant-stream-1", role: "assistant", text: "欢迎回来" },
  ]);

  const updatedStreamingLine = upsertTranscriptLine(updatedByTemporaryId, {
    id: "assistant-stream-1",
    role: "assistant",
    text: "欢迎回来，咱们接着聊。",
  });
  assert.deepEqual(updatedStreamingLine, [
    { ...userLine, text: "我想聊外婆" },
    assistantLine,
    { id: "assistant-stream-1", role: "assistant", text: "欢迎回来，咱们接着聊。" },
  ]);
});

test("counts transcript characters from user lines only", () => {
  assert.equal(countTextChars(" 我 想聊 外婆 "), 5);
  assert.equal(totalTranscriptChars([userLine, assistantLine]), 5);
});

test("follow-up capability follows interview purpose for historical and new sessions", () => {
  for (const guidanceMode of ["free_narration", "guided_interview"]) {
    const session = { guidanceMode, interviewType: "ai_interview", sessionGoal: "open_narration" };
    assert.equal(canRequestFollowUp([], session, "", false), true);
    assert.equal(canRequestFollowUp([], session, "opening", false), false);
    assert.equal(canRequestFollowUp([], session, "", true), false);
    for (const excluded of [
      { ...session, interviewType: "human_interview" },
      { ...session, sessionGoal: "book_completion" },
    ]) {
      assert.equal(canRequestFollowUp([], excluded, "", false), false);
      assert.equal(assistantTranscriptState([], {
        ...excluded, lastFollowUpSourceMessageId: "", followUpLoading: false, canRequestFollowUp: true,
      }).canRequestFollowUp, false);
    }
  }
});
