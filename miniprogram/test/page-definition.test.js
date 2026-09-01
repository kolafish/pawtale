require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule } = require("./page-harness");

test("page runtime values are created per instance at the start of onLoad", () => {
  let registeredPage = null;
  global.Page = (definition) => {
    registeredPage = definition;
  };
  const modulePath = clearModule("../services/page-definition.ts");
  const { definePage, pageRuntime } = require(modulePath);
  let runtimeId = 0;

  class RuntimeController {
    constructor() {
      this.id = ++runtimeId;
    }
  }

  definePage()({
    controller: pageRuntime(() => new RuntimeController()),
    data: {},
    onLoad(value) {
      this.loadedValue = value;
      this.loadedController = this.controller;
    },
  });

  assert.ok(registeredPage);
  assert.equal("controller" in registeredPage, false);
  const first = { ...registeredPage };
  const second = { ...registeredPage };
  registeredPage.onLoad.call(first, "first");
  registeredPage.onLoad.call(second, "second");

  assert.ok(first.controller instanceof RuntimeController);
  assert.ok(second.controller instanceof RuntimeController);
  assert.notEqual(first.controller, second.controller);
  assert.equal(first.loadedController, first.controller);
  assert.equal(second.loadedValue, "second");
});
