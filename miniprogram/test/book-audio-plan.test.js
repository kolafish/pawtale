require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { flushAsyncWork } = require("./page-harness");
const { VoiceAssistantTTSProgressRuntime } = require("../services/voice-assistant-tts-progress.ts");
const { playBookAudioPlan, stopBookPlayback } = require("../pages/book/book-tts-player.ts");

const text = "标题🌿\n\n正文。";
function plan(secondStatus = "pending") {
  return {
    version_id: "version-1", source_text_hash: "hash-1", profile_key: "profile-1",
    voice: "voice-1", speed_ratio: 1, ready_count: secondStatus === "succeeded" ? 2 : 1,
    poll_after_ms: 1,
    segments: [
      { index: 0, text_start: 0, text_end: 3, status: "succeeded", audio_file_id: "audio-1", duration_seconds: 1, alignment: [] },
      { index: 1, text_start: 5, text_end: 8, status: secondStatus, audio_file_id: secondStatus === "succeeded" ? "audio-2" : null, duration_seconds: 1, alignment: [] },
    ],
  };
}

function host() {
  let token = 1;
  let active = null;
  let end = null;
  const played = [];
  const prefetched = [];
  const events = [];
  const context = {
    data: {
      error: "", playingTarget: "", ttsBusyTarget: "book", bookTTSActiveText: "",
      bookTTSActiveTarget: "", bookTTSSpokenText: "", bookTTSCurrentText: "",
      bookTTSRemainingText: "", bookTTSProgressPercent: 0,
      bookTTSDurationSeconds: 0, bookTTSAlignment: [],
    },
    playToken: token,
    ttsProgress: new VoiceAssistantTTSProgressRuntime(),
    setData(patch) { Object.assign(this.data, patch); },
    audioPlayback: {
      currentToken: () => token,
      currentAudio: () => active,
      async prefetchFile(id) { prefetched.push(id); },
      playFile(id, candidate, options) {
        played.push(id);
        active = { currentTime: 0, duration: 1 };
        events.push({ audio: active, options });
        return new Promise((resolve) => {
          end = () => {
            active.currentTime = 1;
            options.onEnded(active);
            active = null;
            resolve();
          };
          options.onPlay(active);
        });
      },
      stop() { token += 1; if (active) end(); },
    },
  };
  return { context, played, prefetched, events, finish: () => end() };
}

test("full-book audio plays the first ready segment and prepares the next during playback", async () => {
  const h = host();
  const requests = [];
  const playback = playBookAudioPlan(h.context, plan(), 1, text, async (index, current, retry) => {
    requests.push([index, current.version_id, retry]);
    return plan("succeeded");
  });
  await flushAsyncWork();
  assert.deepEqual(h.played, ["audio-1"]);
  assert.deepEqual(h.prefetched, ["audio-2"]);
  assert.deepEqual(requests, [[1, "version-1", false]]);
  h.finish();
  await flushAsyncWork();
  assert.deepEqual(h.played, ["audio-1", "audio-2"]);
  assert.ok(h.context.data.bookTTSProgressPercent < 100);
  assert.ok(h.context.data.bookTTSSpokenText.startsWith("标题🌿"));
  h.finish();
  await playback;
  assert.equal(h.context.data.bookTTSSpokenText, text);
  assert.equal(h.context.data.bookTTSProgressPercent, 100);
});

test("stopping while preparation is in flight prevents download and next playback", async () => {
  const h = host();
  let finishRequest;
  const playback = playBookAudioPlan(h.context, plan(), 1, text, () => new Promise((resolve) => { finishRequest = resolve; }));
  await flushAsyncWork();
  stopBookPlayback(h.context);
  finishRequest(plan("succeeded"));
  await playback;
  await flushAsyncWork();
  assert.deepEqual(h.played, ["audio-1"]);
  assert.deepEqual(h.prefetched, []);
  assert.equal(h.context.data.playingTarget, "");
});

test("stopping during polling cancels the timer and does not request another segment", async () => {
  const h = host();
  const pending = plan();
  pending.segments[0].status = "running";
  pending.segments[0].audio_file_id = null;
  let requests = 0;
  const playback = playBookAudioPlan(h.context, pending, 1, text, async () => { requests += 1; return pending; });
  assert.match(h.context.data.bookTTSPreparingText, /第 1 段/);
  assert.equal(h.context.bookTTSPollTimer.pending, true);
  stopBookPlayback(h.context);
  await playback;
  assert.equal(h.context.bookTTSPollTimer.pending, false);
  assert.equal(requests, 0);
  assert.deepEqual(h.played, []);
});

test("stopping after an audio segment ends prevents late preparation from advancing the queue", async () => {
  const h = host();
  let finishRequest;
  let requests = 0;
  const playback = playBookAudioPlan(h.context, plan(), 1, text, () => {
    requests += 1;
    return new Promise((resolve) => { finishRequest = resolve; });
  });
  await flushAsyncWork();
  h.finish();
  await flushAsyncWork();
  stopBookPlayback(h.context);
  finishRequest(plan());
  await playback;
  assert.equal(requests, 1);
  assert.deepEqual(h.played, ["audio-1"]);
});

test("the player rejects another book version and reports failures without automatic retries", async () => {
  for (const mismatch of [true, false]) {
    const h = host();
    let currentHighlight = 5;
    h.context.onBookTTSOffset = offset => { currentHighlight = offset; };
    let requests = 0;
    const playback = playBookAudioPlan(h.context, plan(), 1, text, async () => {
      requests += 1;
      const next = plan("failed");
      if (mismatch) next.version_id = "version-2";
      else next.segments[1].error_message = "生成失败";
      return next;
    });
    const rejected = assert.rejects(playback, mismatch ? /清单已变化/ : /生成失败/);
    await flushAsyncWork();
    h.finish();
    await rejected;
    assert.equal(currentHighlight, null, "failed audio clears the inline reader highlight");
    assert.equal(requests, 1);
    assert.deepEqual(h.played, ["audio-1"]);
  }
});

test("late events from stopped audio cannot change a new playback session", async () => {
  const h = host();
  const first = playBookAudioPlan(h.context, plan("succeeded"), 1, text, async () => plan("succeeded"));
  await flushAsyncWork();
  const old = h.events[0];
  stopBookPlayback(h.context);
  await first;
  const second = playBookAudioPlan(h.context, plan("succeeded"), h.context.playToken, text, async () => plan("succeeded"));
  await flushAsyncWork();
  const before = { ...h.context.data };
  old.options.onEnded(old.audio);
  old.options.onPlay(old.audio);
  assert.deepEqual(h.context.data, before);
  stopBookPlayback(h.context);
  await second;
});

test("audio resumes at the saved segment and native start time without replaying earlier chunks", async () => {
  const h = host();
  const positions = [];
  h.context.onBookAudioPosition = (index, seconds) => positions.push([index, seconds]);
  const playback = playBookAudioPlan(h.context, plan("succeeded"), 1, text, async () => plan("succeeded"), "book", { index: 1, seconds: 0.4 });
  await flushAsyncWork();
  assert.deepEqual(h.played, ["audio-2"]);
  assert.equal(h.events[0].options.startTime, 0.4);
  h.events[0].audio.currentTime = 0.6;
  h.events[0].options.onTimeUpdate(h.events[0].audio);
  assert.deepEqual(positions, [[1, 0.6]]);
  h.finish();
  await playback;
});
