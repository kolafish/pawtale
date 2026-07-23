require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { setPageNavigationTitle } = require("../services/page-navigation.ts");

test("page navigation title follows the current page context", () => {
  const titles = [];
  global.wx = {
    setNavigationBarTitle(options) {
      titles.push(options.title);
    },
  };

  setPageNavigationTitle("第一次离家");

  assert.deepEqual(titles, ["第一次离家"]);
  delete global.wx;
});

test("page navigation title is safe in non-WeChat test contexts", () => {
  assert.doesNotThrow(() => setPageNavigationTitle("本章正文"));
});
