require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, mockModule } = require("./page-harness");

function loadControllerWithFakeClient() {
  const clients = [];
  class FakeStreamingClient {
    constructor(options, recorderResource) {
      this.options = options;
      this.recorderResource = recorderResource;
      this.closeCalls = [];
      this.startRecordingCalls = [];
      this.stopRecordingCalls = 0;
      this.configureAutoStopCalls = [];
      this.finishSessionCalls = 0;
      clients.push(this);
    }

    async connect() {}

    startRecording(options) {
      this.startRecordingCalls.push(options);
      return true;
    }

    stopRecording() {
      this.stopRecordingCalls += 1;
    }

    configureAutoStop(value) {
      this.configureAutoStopCalls.push(value);
    }

    async finishSession() {
      this.finishSessionCalls += 1;
    }

    close(options) {
      this.closeCalls.push(options || {});
    }
  }

  mockModule("../services/memoir-streaming-asr.ts", {
    MemoirStreamingAsrClient: FakeStreamingClient,
  });
  clearModule("../services/streaming-voice-controller.ts");
  const { StreamingVoiceController } = require("../services/streaming-voice-controller.ts");
  const { sharedRecorderResource } = require("../services/recorder-resource.ts");
  return { StreamingVoiceController, clients, sharedRecorderResource };
}

function connectOptions(callbacks = {}) {
  return {
    voiceSessionId: "voice-1",
    guidanceMode: "free_narration",
    callbacks: {
      onStatus: () => {},
      onEvent: () => {},
      onPartial: () => {},
      onFinal: () => {},
      onError: () => {},
      ...callbacks,
    },
  };
}

test("StreamingVoiceController replaces the previous client and ignores stale callbacks", async () => {
  const { StreamingVoiceController, clients, sharedRecorderResource } = loadControllerWithFakeClient();
  const partials = [];
  const controller = new StreamingVoiceController();

  await controller.connect(connectOptions({ onPartial: (text) => partials.push(`first:${text}`) }));
  const firstClient = clients[0];
  await controller.connect(connectOptions({ onPartial: (text) => partials.push(`second:${text}`) }));
  const secondClient = clients[1];

  firstClient.options.callbacks.onPartial("old");
  secondClient.options.callbacks.onPartial("new");

  assert.equal(controller.currentClient(), secondClient);
  assert.equal(firstClient.recorderResource, sharedRecorderResource);
  assert.equal(secondClient.recorderResource, sharedRecorderResource);
  assert.deepEqual(firstClient.closeCalls, [{ force: true }]);
  assert.deepEqual(partials, ["second:new"]);
});

test("StreamingVoiceController forwards diagnostics and drops a closed socket client", async () => {
  const { StreamingVoiceController, clients } = loadControllerWithFakeClient();
  const events = [];
  const statuses = [];
  const controller = new StreamingVoiceController();

  await controller.connect(connectOptions({
    onStatus: (status) => statuses.push(status),
    onClientEvent: (event) => events.push(event),
  }));
  const client = clients[0];
  client.options.callbacks.onClientEvent({ type: "socket_close", frameCount: 0, audioBytesSent: 0 });
  client.options.callbacks.onStatus("closed");

  assert.deepEqual(events, [{ type: "socket_close", frameCount: 0, audioBytesSent: 0 }]);
  assert.deepEqual(statuses, ["closed"]);
  assert.equal(controller.hasClient(), false);
  assert.equal(controller.startRecording(), false);
});

test("StreamingVoiceController force closes a client after recorder failure", async () => {
  const { StreamingVoiceController, clients } = loadControllerWithFakeClient();
  const statuses = [];
  const controller = new StreamingVoiceController();

  await controller.connect(connectOptions({
    onStatus: (status) => statuses.push(status),
  }));
  const client = clients[0];
  client.options.callbacks.onStatus("error");

  assert.deepEqual(statuses, ["error"]);
  assert.deepEqual(client.closeCalls, [{ force: true }]);
  assert.equal(controller.hasClient(), false);
});

test("StreamingVoiceController proxies recording, auto-stop, and close to the current client", async () => {
  const { StreamingVoiceController, clients } = loadControllerWithFakeClient();
  const controller = new StreamingVoiceController();

  await controller.connect(connectOptions());
  const client = clients[0];

  assert.equal(controller.hasClient(), true);
  assert.equal(controller.startRecording({ noVoiceActivityTimeoutMs: 1200 }), true);
  controller.stopRecording();
  controller.configureAutoStop(800);
  controller.close({ force: true });

  assert.deepEqual(client.startRecordingCalls, [{ noVoiceActivityTimeoutMs: 1200 }]);
  assert.equal(client.stopRecordingCalls, 1);
  assert.deepEqual(client.configureAutoStopCalls, [800]);
  assert.deepEqual(client.closeCalls, [{ force: true }]);
  assert.equal(controller.hasClient(), false);
});

test("StreamingVoiceController finishes the current session and clears the client", async () => {
  const { StreamingVoiceController, clients } = loadControllerWithFakeClient();
  const controller = new StreamingVoiceController();

  await controller.connect(connectOptions());
  const client = clients[0];

  assert.equal(await controller.finishSession(), true);

  assert.equal(client.finishSessionCalls, 1);
  assert.equal(controller.hasClient(), false);
  assert.equal(await controller.finishSession(), false);
});

test("StreamingVoiceController clears failed connections", async () => {
  const clients = [];
  class FailingClient {
    constructor(options) {
      this.options = options;
      this.closeCalls = [];
      clients.push(this);
    }

    async connect() {
      throw new Error("connect failed");
    }

    close(options) {
      this.closeCalls.push(options || {});
    }
  }
  mockModule("../services/memoir-streaming-asr.ts", {
    MemoirStreamingAsrClient: FailingClient,
  });
  clearModule("../services/streaming-voice-controller.ts");
  const { StreamingVoiceController } = require("../services/streaming-voice-controller.ts");
  const controller = new StreamingVoiceController();

  await assert.rejects(controller.connect(connectOptions()), /connect failed/);

  assert.equal(controller.hasClient(), false);
  assert.deepEqual(clients[0].closeCalls, [{ force: true }]);
});
