require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const { fileSystemWx } = require("./file-system-mock");

function setup() {
  let cookie = "session=user-a";
  let key = "a".repeat(64);
  let denied = false;
  let hold = false;
  let finish;
  let failDownload = false;
  const downloads = [];
  const requests = [];
  global.wx = {
    ...fileSystemWx(),
    getExtConfigSync: () => ({}),
    getStorageSync: (name) => name === "pawtale_backend_cookie" ? cookie : name === "pawtale_backend_config" ? { httpBaseUrl: "https://backend.local" } : "",
    setStorageSync() {}, removeStorageSync() {},
    request(options) {
      requests.push(options);
      const params = new URL(options.url).searchParams;
      options.success({ statusCode: denied ? 404 : 200, data: denied ? { detail: "项目不存在" } : {
        version_id: params.get("version_id") || "version-28", cache_key: key,
        filename: "《写给他的话》_阅读版_v28.pdf", ready: true,
      }, header: {} });
    },
    downloadFile(options) {
      downloads.push(options);
      finish = () => failDownload ? options.fail({ errMsg: "download failed" }) : options.success({ statusCode: 200, tempFilePath: `${wx.env.USER_DATA_PATH}/download.pdf`, header: {} });
      if (!hold) finish();
    },
  };
  function api() {
    ["memoir-book-api", "authenticated-media", "memoir-api-client", "auth", "request"].forEach((name) => delete require.cache[require.resolve(`../services/${name}.ts`)]);
    return require("../services/memoir-book-api.ts").downloadMemoirBookOutputExport;
  }
  return { api, downloads, requests, cookie: (value) => { cookie = value; }, key: (value) => { key = value; }, deny: () => { denied = true; }, hold: () => { hold = true; }, fail: (value) => { failDownload = value; }, finish: () => finish() };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("reopening after a page/module reload uses a named local PDF and still checks server permission", async () => {
  const state = setup();
  const phases = [];
  const file = await state.api()("project-1", "pdf", "version-28", "standard", false, undefined, (phase) => phases.push(phase));
  assert.ok(file.endsWith("/《写给他的话》_阅读版_v28.pdf"));
  assert.deepEqual(phases, ["checking", "downloading", "opening"]);
  const reopened = await state.api()("project-1", "pdf", "version-28");
  assert.equal(reopened, file);
  assert.equal(state.downloads.length, 1);
  assert.equal(state.requests.length, 2);
  state.deny();
  await assert.rejects(state.api()("project-1", "pdf", "version-28"), /项目不存在/);
  assert.equal(state.downloads.length, 1);
});

test("simultaneous opens download once, changed signatures and discarded files download again", async () => {
  const state = setup();
  const download = state.api();
  state.hold();
  const first = download("project-1");
  const second = download("project-1");
  await tick();
  assert.equal(state.downloads.length, 1);
  state.finish();
  const [file, duplicate] = await Promise.all([first, second]);
  assert.equal(duplicate, file);
  fs.unlinkSync(file);
  const missing = download("project-1");
  await tick(); state.finish(); await missing;
  assert.equal(state.downloads.length, 2);
  state.key("b".repeat(64));
  const changed = download("project-1");
  await tick(); state.finish();
  assert.notEqual(await changed, file);
  assert.equal(state.downloads.length, 3);
});

test("failed transfers retry and an account switch cannot expose a late download", async () => {
  const state = setup();
  const download = state.api();
  state.fail(true);
  await assert.rejects(download("project-1"), /download failed/);
  state.fail(false); state.hold();
  const next = download("project-1");
  await tick(); state.cookie("session=user-b"); state.finish();
  await assert.rejects(next, (error) => error.statusCode === 409 && /登录状态已变化/.test(error.message));
  state.key("c".repeat(64));
  const other = download("project-1");
  await tick(); state.finish(); await other;
  assert.equal(state.downloads.length, 3);
});

test("saved exports evict old files and keep the current named PDF", async () => {
  const state = setup();
  const download = state.api();
  let file;
  for (let index = 1; index <= 22; index += 1) {
    state.key(index.toString(16).padStart(64, "0"));
    file = await download("project-1");
  }
  const root = `${wx.env.USER_DATA_PATH}/pawtale-exports-v1`;
  assert.equal(fs.readdirSync(root).length, 20);
  assert.ok(fs.statSync(file).size > 0);
});


test("large incoming PDFs evict old cached files before saving within the storage budget", async () => {
  const state = setup();
  const download = state.api();
  fs.writeFileSync(`${wx.env.USER_DATA_PATH}/download.pdf`, Buffer.alloc(20 * 1024 * 1024, 1));
  const native = wx.getFileSystemManager();
  wx.getFileSystemManager = () => ({ ...native, copyFileSync(source, dest) {
    const root = `${wx.env.USER_DATA_PATH}/pawtale-exports-v1`;
    const existingBytes = fs.readdirSync(root).reduce((sum, key) => sum + fs.readdirSync(`${root}/${key}`).reduce((bytes, file) => bytes + fs.statSync(`${root}/${key}/${file}`).size, 0), 0);
    if (existingBytes + fs.statSync(source).size > 40 * 1024 * 1024) throw new Error("quota exceeded");
    native.copyFileSync(source, dest);
  } });
  for (let index = 1; index <= 3; index += 1) {
    state.key(index.toString(16).padStart(64, "0"));
    await download("project-1");
  }
  assert.equal(fs.readdirSync(`${wx.env.USER_DATA_PATH}/pawtale-exports-v1`).length, 2);
});
