require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  clearSourceChangeMetadata,
  removedSourceCount,
  sourceSupplementCount,
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

test("source change helpers count typed metadata and legacy draft reasons", () => {
  assert.equal(
    sourceSupplementCount(
      { pending_source_utterance_ids: ["utterance-1", "", 42, "utterance-2"] },
      { metadata_json: { status_reason: "sources_deleted" } },
    ),
    2,
  );
  assert.equal(
    sourceSupplementCount({}, { metadata_json: { status_reason: "new_sources_assigned" } }),
    1,
  );
  assert.equal(
    removedSourceCount(
      { removed_source_utterance_ids: ["utterance-3"] },
      { metadata_json: { status_reason: "new_sources_assigned" } },
    ),
    1,
  );
  assert.equal(
    removedSourceCount({}, { metadata_json: { status_reason: "sources_deleted" } }),
    1,
  );
});

test("clearSourceChangeMetadata only removes source-change bookkeeping keys", () => {
  assert.deepEqual(
    clearSourceChangeMetadata({
      pending_source_utterance_ids: ["utterance-1"],
      source_assignment_updated_at: "2026-06-17T00:00:00Z",
      source_refs_updated_at: "2026-06-17T01:00:00Z",
      removed_source_utterance_ids: ["utterance-2"],
      source_refs_removed_at: "2026-06-18T00:00:00Z",
      title_updated_by: "user",
    }),
    { title_updated_by: "user" },
  );
});
