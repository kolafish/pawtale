require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

test("configureAudiblePlayback disables mute switch and sets full volume", () => {
  const calls = [];
  global.wx = {
    setInnerAudioOption(options) {
      calls.push(options);
    },
  };
  const audio = {
    obeyMuteSwitch: true,
    volume: 0.2,
  };
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { configureAudiblePlayback } = require("../services/audio-playback.ts");

  configureAudiblePlayback(audio);

  assert.deepEqual(calls, [{ obeyMuteSwitch: false, mixWithOther: true }]);
  assert.equal(audio.obeyMuteSwitch, false);
  assert.equal(audio.volume, 1);

  delete global.wx;
});
