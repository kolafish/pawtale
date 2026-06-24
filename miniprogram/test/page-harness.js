function clearModule(relativePath) {
  const modulePath = require.resolve(relativePath);
  delete require.cache[modulePath];
  return modulePath;
}

function mockModule(relativePath, exports) {
  const modulePath = clearModule(relativePath);
  require.cache[modulePath] = {
    id: modulePath,
    filename: modulePath,
    loaded: true,
    exports,
  };
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

module.exports = {
  clearModule,
  loadPageModule,
  mockModule,
  pageContext,
};
