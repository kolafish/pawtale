require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  uniqueSourceRefs,
} = require("../services/memoir-book-sources.ts");

function sourceRef(overrides = {}) {
  return {
    voice_session_id: "voice-1",
    message_id: "message-1",
    utterance_id: "utterance-1",
    quote: "往事",
    ...overrides,
  };
}

test("uniqueSourceRefs deduplicates by stable source identity", () => {
  const refs = uniqueSourceRefs([
    sourceRef({ utterance_id: "utterance-1", quote: "第一条" }),
    sourceRef({ utterance_id: "utterance-1", quote: "重复条" }),
    sourceRef({ utterance_id: "", voice_session_id: "voice-2", message_id: "message-2" }),
    sourceRef({ utterance_id: "", voice_session_id: "voice-2", message_id: "message-2", quote: "重复兜底" }),
  ]);

  assert.deepEqual(
    refs.map((ref) => ref.quote),
    ["第一条", "往事"],
  );
});
