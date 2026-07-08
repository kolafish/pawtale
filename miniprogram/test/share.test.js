require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

test("share menu enables friend and timeline entries when available", () => {
  const calls = [];
  global.wx = {
    showShareMenu(options) {
      calls.push(options);
    },
  };
  try {
    const { enableMiniProgramShareMenu } = require("../services/share.ts");

    enableMiniProgramShareMenu();

    assert.deepEqual(calls, [{
      menus: ["shareAppMessage", "shareTimeline"],
    }]);
  } finally {
    delete global.wx;
  }
});

test("book share title falls back to the manuscript entry when no book exists", () => {
  const { bookShareAppMessage } = require("../services/share.ts");

  assert.deepEqual(bookShareAppMessage({ projectTitle: "爷爷的故事", hasBookContent: false }), {
    title: "一起整理《爷爷的故事》的书稿",
    path: "/pages/book/index?from=share_book",
  });
});
