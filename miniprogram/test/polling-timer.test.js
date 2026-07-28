require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const { PollingTimer } = require("../services/polling-timer.ts");

function manualScheduler() {
  let nextId = 0;
  const callbacks = new Map();
  return {
    scheduler: {
      schedule(callback) {
        nextId += 1;
        callbacks.set(nextId, callback);
        return nextId;
      },
      cancel(timer) {
        callbacks.delete(timer);
      },
    },
    flush() {
      const pending = [...callbacks.entries()];
      callbacks.clear();
      pending.forEach(([, callback]) => callback());
    },
    pendingCount() {
      return callbacks.size;
    },
  };
}

test("polling timer owns one scheduled callback at a time", () => {
  const manual = manualScheduler();
  const timer = new PollingTimer(1500, manual.scheduler);
  let calls = 0;

  assert.equal(timer.schedule(() => {
    calls += 1;
  }), true);
  assert.equal(timer.schedule(() => {
    calls += 1;
  }), false);
  assert.equal(timer.pending, true);
  assert.equal(manual.pendingCount(), 1);

  manual.flush();

  assert.equal(calls, 1);
  assert.equal(timer.pending, false);
  assert.equal(timer.schedule(() => {
    calls += 1;
  }), true);
});

test("polling timer cancellation releases the scheduled callback", () => {
  const manual = manualScheduler();
  const timer = new PollingTimer(1500, manual.scheduler);
  let calls = 0;

  timer.schedule(() => {
    calls += 1;
  });
  timer.cancel();
  manual.flush();

  assert.equal(calls, 0);
  assert.equal(timer.pending, false);
  assert.equal(manual.pendingCount(), 0);
});
