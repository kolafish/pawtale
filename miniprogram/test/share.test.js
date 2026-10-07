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
