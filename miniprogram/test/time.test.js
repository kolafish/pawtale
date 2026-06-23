require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function timeModule() {
  delete require.cache[require.resolve("../services/time.ts")];
  return require("../services/time.ts");
}

test("formats explicit UTC timestamps as Beijing time", () => {
  const { formatBeijingDateTime } = timeModule();

  assert.equal(formatBeijingDateTime("2026-06-16T01:05:00+00:00"), "06-16 09:05 北京时间");
  assert.equal(formatBeijingDateTime("2026-12-31T16:30:00Z"), "01-01 00:30 北京时间");
});

test("treats legacy timezone-less backend timestamps as UTC", () => {
  const { formatBeijingDateTime } = timeModule();

  assert.equal(formatBeijingDateTime("2026-06-16T01:05:00"), "06-16 09:05 北京时间");
  assert.equal(formatBeijingDateTime(""), "");
});

test("formats Beijing time without timezone label for compact records", () => {
  const { formatBeijingDateTimeShort } = timeModule();

  assert.equal(formatBeijingDateTimeShort("2026-06-16T01:05:00+00:00"), "06-16 09:05");
  assert.equal(formatBeijingDateTimeShort(""), "");
});
