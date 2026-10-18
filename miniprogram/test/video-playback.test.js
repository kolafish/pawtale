require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, pageContext } = require("./page-harness");

const playerPages = ["share-video-player", "transcript-media", "image-assets"];

for (const name of playerPages) {
  test(`${name} changes native playback speed without restarting playback`, () => {
    const calls = [];
    global.wx = {
      createVideoContext(id, scope) {
        calls.push({ id, scope });
        // Deliberately expose no play/seek: changing speed must preserve pause and position.
        return { playbackRate: (rate) => calls.push(rate) };
      },
    };
    const { page } = loadPageModule(`../pages/${name}/index.ts`);
    const context = pageContext(page);
    assert.equal(context.data.videoPlaybackRate, 1);

    for (const rate of [1.25, 1.5, 2, 1]) {
      context.toggleVideoSpeedMenu();
      assert.equal(context.data.videoSpeedMenuOpen, true);
      context.changeVideoPlaybackRate({ currentTarget: { dataset: { rate: String(rate) } } });
      assert.equal(context.data.videoPlaybackRate, rate);
      assert.equal(context.data.videoSpeedMenuOpen, false);
      assert.deepEqual(calls.splice(0), [{ id: "memoir-video", scope: context }, rate]);
    }

    for (const rate of [undefined, "", "bad", 0, -1, 1.7, 4]) {
      context.changeVideoPlaybackRate({ currentTarget: { dataset: { rate } } });
    }
    assert.deepEqual(calls, []);
    assert.equal(context.data.videoPlaybackRate, 1);

    context.changeVideoPlaybackRate({ currentTarget: { dataset: { rate: 1.5 } } });
    calls.length = 0;
    // Each play event, including a recreated player, reapplies the selected speed.
    context.applyVideoPlaybackRate();
    assert.deepEqual(calls.splice(0), [{ id: "memoir-video", scope: context }, 1.5]);
    context.toggleVideoSpeedMenu();
    context.closeVideoSpeedMenu();
    assert.equal(context.data.videoSpeedMenuOpen, false);
    assert.equal(context.data.videoPlaybackRate, 1.5);
    assert.deepEqual(calls, []);
    assert.equal(pageContext(page).data.videoPlaybackRate, 1);
  });

  test(`${name} binds the shared speed control inside the native video`, () => {
    const source = fs.readFileSync(path.join(__dirname, `../pages/${name}/index.wxml`), "utf8");
    const video = source.match(/<video\b([\s\S]*?)>([\s\S]*?)<\/video>/);
    assert.ok(video, "video must contain its speed control so it can follow fullscreen");
    assert.match(video[1], /\bid="memoir-video"/);
    assert.match(video[1], /\bbindplay="applyVideoPlaybackRate"/);
    assert.match(video[1], /\bbindfullscreenchange="closeVideoSpeedMenu"/);
    assert.match(video[2], /<template\s+is="videoPlaybackSpeed"/);
    assert.match(source, /<import\s+src="\.\.\/\.\.\/templates\/video-playback\.wxml"/);
  });
}
