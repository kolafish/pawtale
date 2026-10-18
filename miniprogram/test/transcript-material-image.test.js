require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  initialTranscriptMaterialImageTaskState,
  TranscriptMaterialImageController,
} = require("../services/transcript-material-image.ts");

test("image generation keeps its phase label while polls are in flight and still reports completion", async () => {
  const runningJob = {
    job_id: "image-job-1",
    status: "running",
    asset_status: "running",
    stage: "generating",
    error_message: "",
  };
  let resolvePoll;
  let scheduledPoll;
  let state = initialTranscriptMaterialImageTaskState();
  const labels = [];
  const controller = new TranscriptMaterialImageController({
    createJob: async () => runningJob,
    getJob: () => new Promise((resolve) => { resolvePoll = resolve; }),
    schedule(callback) { scheduledPoll = callback; return 1; },
    cancelSchedule() {},
  });
  controller.setStateListener((patch) => {
    state = { ...state, ...patch };
    labels.push(state.materialImageStatusText);
  });

  try {
    await controller.create({ utteranceId: "utterance-1", startChar: 0, endChar: 13, styleId: "watercolor" });
    const generatingLabel = state.materialImageStatusText;
    labels.length = 0;

    // Real timer polls must not replace the phase label while waiting for the response.
    for (let index = 0; index < 2; index += 1) {
      scheduledPoll();
      assert.equal(state.materialImageStatusText, generatingLabel);
      resolvePoll(runningJob);
      await Promise.resolve();
    }
    assert.ok(labels.length > 0);
    assert.ok(labels.every((label) => label === generatingLabel));
    assert.equal(state.materialImagePolling, true);

    scheduledPoll();
    resolvePoll({ ...runningJob, status: "succeeded", asset_status: "ready", stage: "ready" });
    await Promise.resolve();
    assert.equal(state.materialImagePolling, false);
    assert.equal(state.materialImageStatusClass, "succeeded");
    assert.notEqual(state.materialImageStatusText, generatingLabel);
  } finally {
    controller.dispose();
  }
});
