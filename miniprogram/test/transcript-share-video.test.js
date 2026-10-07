require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { ApiError } = require("../services/request.ts");
const {
  initialTranscriptShareVideoTaskState,
  shareVideoBackgroundPatch,
  shareVideoStylePatch,
  TranscriptShareVideoController,
} = require("../services/transcript-share-video.ts");

test("illustration subsettings preserve the selected video style", () => {
  const context = {
    default_background_style: "watercolor",
    default_background_mode: "auto",
    background_styles: [{ value: "watercolor", label: "水彩" }],
    background_modes: [{ value: "auto", label: "自动" }],
  };
  const patch = shareVideoBackgroundPatch(context, {
    backgroundStyle: "watercolor",
    backgroundMode: "auto",
  });
  for (const style of ["uploaded", "illustrated", "story_animation"]) {
    const selected = shareVideoStylePatch(context, style);
    const state = { ...selected, ...patch };
    assert.equal(state.shareVideoStyle, selected.shareVideoStyle);
    assert.equal(state.shareVideoBackgroundSource, selected.shareVideoBackgroundSource);
    assert.equal(state.shareVideoBackgroundUsesAi, selected.shareVideoBackgroundUsesAi);
    assert.equal(state.shareVideoBackgroundUsesCustom, selected.shareVideoBackgroundUsesCustom);
    assert.equal(state.shareVideoBackgroundStyle, "watercolor");
    assert.equal(state.shareVideoBackgroundMode, "auto");
  }
});

test("video style selection uses the project default and keeps unavailable illustration explicit", () => {
  assert.equal(shareVideoStylePatch({ default_video_style: "story_animation" }).shareVideoStyle, "story_animation");
  const patch = shareVideoStylePatch({ default_video_style: "illustrated", ai_background_enabled: false });
  assert.equal(patch.shareVideoStyle, "illustrated");
  assert.equal(patch.shareVideoStyleUnavailable, true);
  assert.equal(shareVideoStylePatch(null, "uploaded").shareVideoBackgroundUsesCustom, true);
});

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
    updated_at: "2026-07-29T06:00:00.000Z",
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
    streamUrl: (job) => `https://api.example.com/shares/${job.id}/video`,
    shareFile: async () => {},
    saveVideo: async () => {},
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
  let pollCount = 0;
  const runningJob = shareVideoJob({
    status: "running",
    background_status: "generating",
    background_effective_mode: "smart_multi",
    background_scene_count: 3,
    output: null,
  });
  const { controller, patches, state } = controllerFixture({
    getJob: async () => {
      pollCount += 1;
      return {
        ...runningJob,
        updated_at: `2026-07-29T06:00:0${pollCount}.000Z`,
      };
    },
  });

  controller.resume("job-1");
  await controller.refresh("job-1");
  await controller.refresh("job-1");

  assert.equal(state().shareVideoPolling, true);
  assert.equal(state().shareVideoStatusText, "正在生成多张背景图");
  assert.equal(patches.length, 2);
  assert.deepEqual(
    patches
      .filter((patch) => Object.hasOwn(patch, "shareVideoStatusText"))
      .map((patch) => patch.shareVideoStatusText),
    ["正在生成视频", "正在生成多张背景图"],
  );
  controller.dispose();
});

test("share video controller presents background work as forward-only stages", async () => {
  const jobs = [
    shareVideoJob({
      status: "running",
      background_status: "pending",
      background_scene_count: 0,
      output: null,
      updated_at: "2026-07-29T06:00:01.000Z",
    }),
    shareVideoJob({
      status: "running",
      background_status: "planning",
      background_scene_count: 0,
      output: null,
      updated_at: "2026-07-29T06:00:02.000Z",
    }),
    shareVideoJob({
      status: "running",
      background_status: "generating",
      background_scene_count: 1,
      output: null,
      updated_at: "2026-07-29T06:00:03.000Z",
    }),
    shareVideoJob({
      status: "running",
      background_status: "ready",
      background_scene_count: 1,
      output: null,
      updated_at: "2026-07-29T06:00:04.000Z",
    }),
  ];
  const { controller, patches, state } = controllerFixture({
    getJob: async () => jobs.shift(),
  });

  controller.resume("job-1");
  await controller.refresh("job-1");
  await controller.refresh("job-1");
  await controller.refresh("job-1");
  await controller.refresh("job-1");

  assert.equal(state().shareVideoStatusText, "正在合成视频");
  assert.deepEqual(
    patches
      .filter((patch) => Object.hasOwn(patch, "shareVideoStatusText"))
      .map((patch) => patch.shareVideoStatusText),
    ["正在生成视频", "正在准备背景图", "正在生成背景图", "正在合成视频"],
  );
  controller.dispose();
});

test("share video controller streams the finished clip and downloads it only to save or send", async () => {
  const pollCalls = [];
  const downloads = [];
  const saved = [];
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
    saveVideo: async (filePath) => {
      saved.push(filePath);
    },
  });

  controller.resume("job-1");
  await controller.refresh("job-1");

  assert.deepEqual(pollCalls, ["job-1"]);
  assert.equal(state().shareVideoPolling, true);
  assert.equal(state().shareVideoStatusText, "视频状态刷新中断，正在重试");

  await controller.refresh("job-1");

  assert.deepEqual(pollCalls, ["job-1", "job-1"]);
  assert.equal(state().shareVideoJob.status, "succeeded");
  assert.equal(state().shareVideoStreamUrl, "https://api.example.com/shares/job-1/video");
  assert.deepEqual(downloads, []);

  assert.equal(await controller.saveToAlbum(), true);
  assert.deepEqual(downloads, ["job-1"]);
  assert.deepEqual(saved, ["/tmp/share-video.mp4"]);
  assert.equal(state().shareVideoStatusText, "已保存到相册");
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
