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

test("private book pages hide the mini program share menu", () => {
  const calls = [];
  global.wx = {
    hideShareMenu() {
      calls.push("hidden");
    },
  };
  try {
    const { hideMiniProgramShareMenu } = require("../services/share.ts");

    hideMiniProgramShareMenu();

    assert.deepEqual(calls, ["hidden"]);
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

test("reading card excerpt keeps real text and Unicode offsets, skipping headings", () => {
  const { readingShareExcerpt } = require("../services/share.ts");
  const content = "# 家里的故事\n\n  🌿奶奶坐在门口。后来我们回家了。";
  const excerpt = readingShareExcerpt(content, "version-1");
  assert.equal(excerpt.text, "🌿奶奶坐在门口。");
  assert.equal(Array.from(content).slice(excerpt.start, excerpt.start + excerpt.length).join(""), excerpt.text);
  assert.equal(excerpt.versionId, "version-1");
  assert.equal(readingShareExcerpt("# 只有标题\n", "v"), undefined);
  assert.equal(readingShareExcerpt("一".repeat(60), "v").length, 48);
});

test("reading share and forwarding use the dedicated public card without an implicit excerpt", () => {
  global.wx = { getStorageSync: () => ({ httpBaseUrl: "https://example.test/" }) };
  try {
    const { readingShareMessage, readingShareImageUrl } = require("../services/share.ts");
    const url = readingShareMessage("真实书名", "c1.token").imageUrl;
    assert.equal(url, "https://example.test/api/mobile/memoir/book-shares/c1.token/card.jpg?design=2");
    const selected = readingShareImageUrl("c1.token", { text: "敏感原文不进URL", start: 5, length: 8, versionId: "version-1" });
    assert.ok(selected.endsWith("&version_id=version-1&excerpt_start=5&excerpt_length=8"));
    assert.ok(!selected.includes("敏感"));
  } finally { delete global.wx; }
});
