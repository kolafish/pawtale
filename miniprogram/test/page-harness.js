const { afterEach } = require("node:test");

const TEST_GLOBAL_NAMES = ["wx", "Page", "Component", "getApp", "getCurrentPages"];
const initialGlobals = new Map(
  TEST_GLOBAL_NAMES.map((name) => [name, Object.prototype.hasOwnProperty.call(global, name)
    ? global[name]
    : undefined]),
);
const initiallyPresentGlobals = new Set(
  TEST_GLOBAL_NAMES.filter((name) => Object.prototype.hasOwnProperty.call(global, name)),
);
const mockedModulePaths = new Set();

function clearModule(relativePath) {
  const modulePath = require.resolve(relativePath);
  delete require.cache[modulePath];
  return modulePath;
}

function mockModule(relativePath, exports) {
  const modulePath = clearModule(relativePath);
  mockedModulePaths.add(modulePath);
  require.cache[modulePath] = {
    id: modulePath,
    filename: modulePath,
    loaded: true,
    exports,
  };
}

function mockMemoirApiModules(exports) {
  [
    "memoir-audio-api",
    "memoir-book-api",
    "memoir-book-completion-api",
    "memoir-chapter-workspace-api",
    "memoir-completion-issue-api",
    "memoir-snapshot-api",
    "memoir-transcript-api",
    "memoir-voice-session-api",
  ].forEach((name) => mockModule(`../services/${name}.ts`, exports));
}

function unmockedAsync(methodName) {
  return async () => {
    throw new Error(`${methodName} was not mocked`);
  };
}

async function flushAsyncWork(rounds = 6) {
  for (let index = 0; index < rounds; index += 1) {
    await Promise.resolve();
  }
}

function loadPageModule(relativePath) {
  const modulePath = clearModule(relativePath);
  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  let mod;
  try {
    mod = require(modulePath);
  } finally {
    if (previousPage === undefined) {
      delete global.Page;
    } else {
      global.Page = previousPage;
    }
  }
  return { mod, page };
}

function loadComponentModule(relativePath) {
  const modulePath = clearModule(relativePath);
  let component = null;
  const previousComponent = global.Component;
  global.Component = (definition) => {
    component = definition;
  };
  let mod;
  try {
    mod = require(modulePath);
  } finally {
    if (previousComponent === undefined) {
      delete global.Component;
    } else {
      global.Component = previousComponent;
    }
  }
  return { mod, component };
}

function pageContext(page, dataPatch = {}) {
  return {
    ...page,
    data: { ...page.data, ...dataPatch },
    setData(patch, callback) {
      this.data = { ...this.data, ...patch };
      callback?.();
    },
  };
}

function componentContext(component, propertiesPatch = {}, dataPatch = {}) {
  const properties = Object.fromEntries(
    Object.entries(component.properties || {}).map(
      ([key, definition]) => [key, definition.value],
    ),
  );
  return {
    ...component.methods,
    properties: { ...properties, ...propertiesPatch },
    data: { ...component.data, ...dataPatch },
    events: [],
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    triggerEvent(name, detail) {
      this.events.push({ name, detail });
    },
  };
}

afterEach(() => {
  mockedModulePaths.forEach((modulePath) => {
    delete require.cache[modulePath];
  });
  mockedModulePaths.clear();
  TEST_GLOBAL_NAMES.forEach((name) => {
    if (initiallyPresentGlobals.has(name)) {
      global[name] = initialGlobals.get(name);
    } else {
      delete global[name];
    }
  });
});

module.exports = {
  clearModule,
  componentContext,
  flushAsyncWork,
  loadComponentModule,
  loadPageModule,
  mockMemoirApiModules,
  mockModule,
  pageContext,
  unmockedAsync,
};
