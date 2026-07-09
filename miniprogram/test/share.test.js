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

test("share menu can limit book workbench sharing to friend messages", () => {
  const calls = [];
  global.wx = {
    showShareMenu(options) {
      calls.push(options);
    },
  };
  try {
    const { enableMiniProgramShareMenu } = require("../services/share.ts");

    enableMiniProgramShareMenu({ includeTimeline: false });

    assert.deepEqual(calls, [{
      menus: ["shareAppMessage"],
    }]);
  } finally {
    delete global.wx;
  }
});

test("local file sharing calls wx.shareFileMessage with file name", async () => {
  const calls = [];
  global.wx = {
    shareFileMessage(options) {
      calls.push(options);
      options.success({ errMsg: "shareFileMessage:ok" });
    },
  };
  try {
    const { shareLocalFileMessage } = require("../services/share.ts");

    await shareLocalFileMessage({ filePath: "/tmp/share.mp4", fileName: "回忆录片段.mp4" });

    assert.equal(calls.length, 1);
    assert.equal(calls[0].filePath, "/tmp/share.mp4");
    assert.equal(calls[0].fileName, "回忆录片段.mp4");
  } finally {
    delete global.wx;
  }
});

test("local file sharing treats user cancellation as a cancel error", async () => {
  global.wx = {
    shareFileMessage(options) {
      options.fail({ errMsg: "shareFileMessage:fail cancel" });
    },
  };
  try {
    const {
      isShareFileMessageCancelError,
      shareLocalFileMessage,
    } = require("../services/share.ts");

    await assert.rejects(
      () => shareLocalFileMessage({ filePath: "/tmp/share.mp4" }),
      (error) => isShareFileMessageCancelError(error),
    );
  } finally {
    delete global.wx;
  }
});

test("local file sharing reports unsupported WeChat versions", async () => {
  global.wx = {};
  try {
    const { shareLocalFileMessage } = require("../services/share.ts");

    await assert.rejects(
      () => shareLocalFileMessage({ filePath: "/tmp/share.mp4" }),
      /当前微信版本不支持直接分享文件/,
    );
  } finally {
    delete global.wx;
  }
});

test("book share opens the finished-book reader when book content exists", () => {
  const { bookReaderSharePath, bookShareAppMessage, bookShareTimeline } = require("../services/share.ts");

  assert.equal(
    bookReaderSharePath("project/1"),
    "/pages/book-reader/index?projectId=project%2F1&from=share_book",
  );
  assert.deepEqual(bookShareAppMessage({
    projectId: "project/1",
    bookTitle: "外婆回忆录",
    hasBookContent: true,
  }), {
    title: "送你一本《外婆回忆录》",
    path: "/pages/book-reader/index?projectId=project%2F1&from=share_book",
  });
  assert.deepEqual(bookShareTimeline({
    projectId: "project/1",
    bookTitle: "外婆回忆录",
    hasBookContent: true,
  }), {
    title: "送你一本《外婆回忆录》",
    query: "projectId=project%2F1&from=share_book",
  });
});

test("book share title falls back to the manuscript entry when no book exists", () => {
  const { bookShareAppMessage } = require("../services/share.ts");

  assert.deepEqual(bookShareAppMessage({ projectTitle: "爷爷的故事", hasBookContent: false }), {
    title: "一起整理《爷爷的故事》的书稿",
    path: "/pages/book/index?from=share_book",
  });
});
