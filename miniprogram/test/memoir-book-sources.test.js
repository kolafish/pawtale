require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { sourceRefKey } = require("../services/memoir-book-sources.ts");

test("source identity prefers utterance id and scopes legacy messages to their session", () => {
  assert.equal(sourceRefKey({ utterance_id: "utterance-1", voice_session_id: "voice-1", message_id: "message-1" }), "utterance-1");
  assert.equal(sourceRefKey({ utterance_id: "", voice_session_id: "voice-1", message_id: "message-1" }), "voice-1:message-1");
  assert.notEqual(
    sourceRefKey({ voice_session_id: "voice-1", message_id: "message-1" }),
    sourceRefKey({ voice_session_id: "voice-2", message_id: "message-1" }),
  );
});
