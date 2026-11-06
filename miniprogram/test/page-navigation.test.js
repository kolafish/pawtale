require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { scrollPageTo, setPageNavigationTitle } = require("../services/page-navigation.ts");

test.afterEach(() => {
  delete global.wx;
  delete global.getCurrentPages;
});

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

test("anchor scrolling uses the visible page height but preserves saved positions and measured player offsets", () => {
  const scrolls = [];
  global.wx = { pageScrollTo: options => scrolls.push(options) };
  global.getCurrentPages = () => [
    { data: { navigationHeight: 91 } },
    { data: { navigationHeight: 195 } },
  ];

  scrollPageTo({ selector: "#reader-body", duration: 280 });
  scrollPageTo({ scrollTop: 640, duration: 280 });
  scrollPageTo({ selector: "#reader-spoken-sentence", offsetTop: -363, duration: 280 });

  assert.deepEqual(scrolls, [
    { selector: "#reader-body", offsetTop: -195, duration: 280 },
    { scrollTop: 640, duration: 280 },
    { selector: "#reader-spoken-sentence", offsetTop: -363, duration: 280 },
  ]);
});
