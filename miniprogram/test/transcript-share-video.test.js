require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { ApiError } = require("../services/request.ts");
const {
  initialTranscriptShareVideoTaskState,
  TranscriptShareVideoController,
} = require("../services/transcript-share-video.ts");

function shareVideoJob(overrides = {}) {
  return {
    id: "job-1",
    status: "succeeded",
    background_source: "ai",
    background_status: "ready",
    background_effective_mode: "single",
    background_scene_count: 1,
    output: {
      mime_type: "video/mp4",
      size_bytes: 4096,
      download_url: "/api/mobile/memoir/share-video/jobs/job-1/download",
    },
    error_message: "",
    ...overrides,
  };
}

function controllerFixture(overrides = {}) {
  let state = initialTranscriptShareVideoTaskState();
  const scheduled = [];
  const patches = [];
  const controller = new TranscriptShareVideoController({
    createJob: async () => shareVideoJob(),
    getJob: async () => shareVideoJob(),
    downloadJob: async () => "/tmp/share-video.mp4",
    shareFile: async () => {},
    schedule(callback) {
      scheduled.push(callback);
      return scheduled.length;
    },
    cancelSchedule() {},
    ...overrides,
  });
  controller.setStateListener((patch) => {
    patches.push(patch);
    state = { ...state, ...patch };
  });
  return {
    controller,
    patches,
    scheduled,
    state: () => state,
  };
}

test("share video controller keeps the current phase label between running polls", async () => {
  const runningJob = shareVideoJob({
    status: "running",
    background_status: "generating",
    background_effective_mode: "smart_multi",
    background_scene_count: 3,
    output: null,
  });
  const { controller, patches, state } = controllerFixture({
    getJob: async () => ({ ...runningJob }),
  });

  controller.resume("job-1");
  await controller.refresh("job-1");
  await controller.refresh("job-1");

  assert.equal(state().shareVideoPolling, true);
  assert.equal(state().shareVideoStatusText, "正在生成多张背景图");
  assert.deepEqual(
    patches
      .filter((patch) => Object.hasOwn(patch, "shareVideoStatusText"))
      .map((patch) => patch.shareVideoStatusText),
    ["正在生成视频", "正在生成多张背景图"],
  );
  controller.dispose();
});

test("share video controller retries transient polling and downloads the completed preview", async () => {
  const pollCalls = [];
  const downloads = [];
  const { controller, state } = controllerFixture({
    getJob: async (jobId) => {
      pollCalls.push(jobId);
      if (pollCalls.length === 1) {
        throw new ApiError(0, "request:fail timeout", {});
      }
      return shareVideoJob();
    },
    downloadJob: async (job) => {
      downloads.push(job.id);
      return "/tmp/share-video.mp4";
    },
  });

  controller.resume("job-1");
  await controller.refresh("job-1");

  assert.deepEqual(pollCalls, ["job-1"]);
  assert.equal(state().shareVideoPolling, true);
  assert.equal(state().shareVideoStatusText, "视频状态刷新中断，正在重试");

  await controller.refresh("job-1");

  assert.deepEqual(pollCalls, ["job-1", "job-1"]);
  assert.deepEqual(downloads, ["job-1"]);
  assert.equal(state().shareVideoJob.status, "succeeded");
  assert.equal(state().shareVideoStatusText, "视频已生成，可以预览");
  assert.equal(state().shareVideoPreviewPath, "/tmp/share-video.mp4");
  controller.dispose();
});

test("share video controller cancels in-flight creation without leaving busy state", async () => {
  let resolveCreate;
  const pendingCreate = new Promise((resolve) => {
    resolveCreate = resolve;
  });
  const { controller, state } = controllerFixture({
    createJob: async () => pendingCreate,
  });

  const creation = controller.create({
    utteranceId: "utterance-1",
    startChar: 0,
    endChar: 10,
    options: { backgroundSource: "classic" },
  });
  assert.equal(state().shareVideoCreating, true);

  controller.cancel();
  resolveCreate(shareVideoJob());

  assert.equal(await creation, null);
  assert.equal(state().shareVideoCreating, false);
  assert.equal(state().shareVideoPolling, false);
  assert.equal(state().shareVideoDownloadingPreview, false);
  assert.equal(state().shareVideoSharing, false);
  assert.equal(state().shareVideoStatusText, "");
  assert.equal(state().shareVideoStatusClass, "");
  controller.dispose();
});
