require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  applySpeakerTranscriptionProofread,
  initialSpeakerTranscriptionReducerState,
  reduceSpeakerTranscriptionEvent,
} = require("../pages/speaker-transcription/speaker-transcription-reducer.ts");

function reduce(state, event, nowMs = 1000) {
  return reduceSpeakerTranscriptionEvent(state, event, {
    runSerial: 1,
    nowMs,
  });
}

test("speaker transcription reducer reconciles finalized speech with the live unknown preview", () => {
  const initial = initialSpeakerTranscriptionReducerState();
  const partial = reduce(initial, {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    text: "今天去了公园。后来又去看了老朋友。",
    start_ms: 0,
    end_ms: 2400,
  });
  const finalized = reduce(partial.state, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    speaker_gender: "female",
    segment_id: "segment-1",
    text: "今天去了公园。",
    start_ms: 0,
    end_ms: 1100,
  }, 2000);

  assert.equal(initial.activePartial.visible, false);
  assert.equal(finalized.state.conversationLines.length, 1);
  assert.equal(finalized.state.conversationLines[0].speakerTitle, "女1");
  assert.equal(finalized.state.activePartial.text, "后来又去看了老朋友。");
  assert.equal(finalized.proofreadRequest.text, "今天去了公园。");
});

test("speaker transcription reducer turns cumulative finals into incremental immutable lines", () => {
  const initial = initialSpeakerTranscriptionReducerState();
  const first = reduce(initial, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "segment-1",
    text: "第一句话。",
    start_ms: 0,
    end_ms: 900,
  });
  const second = reduce(first.state, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "segment-2",
    text: "第一句话。第二句话。",
    start_ms: 0,
    end_ms: 1800,
  }, 2000);

  assert.equal(initial.conversationLines.length, 0);
  assert.deepEqual(
    second.state.conversationLines.map((line) => line.text),
    ["第二句话。", "第一句话。"],
  );
  assert.notEqual(second.state, first.state);
  assert.equal(first.state.conversationLines.length, 1);
});

test("speaker transcription reducer reapplies proofread text and removes it from the live preview", () => {
  const partial = reduce(initialSpeakerTranscriptionReducerState(), {
    type: "speaker.partial",
    speaker_id: "speaker_unknown",
    text: "我是在一九八零年参加工作的。后面去了上海。",
    start_ms: 0,
    end_ms: 3000,
  });
  const finalized = reduce(partial.state, {
    type: "speaker.final",
    speaker_id: "speaker_1",
    segment_id: "segment-1",
    text: "我是在1980年参加工作的。",
    start_ms: 0,
    end_ms: 1600,
  });
  const line = finalized.state.conversationLines[0];
  const proofread = applySpeakerTranscriptionProofread(
    finalized.state,
    line.id,
    {
      text: "我是在一九八零年参加工作的。",
      proofreadLoading: false,
      proofreadStatusText: "已校对",
    },
    true,
  );

  assert.equal(proofread.state.conversationLines[0].text, "我是在一九八零年参加工作的。");
  assert.equal(proofread.state.activePartial.text, "后面去了上海。");
});
