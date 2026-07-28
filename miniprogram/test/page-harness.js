const { afterEach } = require("node:test");

const TEST_GLOBAL_NAMES = ["wx", "Page", "getApp", "getCurrentPages"];
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

function pageContext(page, dataPatch = {}) {
  return {
    ...page,
    data: { ...page.data, ...dataPatch },
    setData(patch) {
      this.data = { ...this.data, ...patch };
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
  flushAsyncWork,
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
};
