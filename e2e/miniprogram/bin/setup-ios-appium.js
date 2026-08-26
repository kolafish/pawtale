#!/usr/bin/env node

const { readIOSRuntimeConfig } = require("../support/config");
const {
  XCUITEST_DRIVER_VERSION,
  ensureXCUITestDriver,
} = require("../support/ios-appium-runtime");

(async () => {
  const config = readIOSRuntimeConfig();
  await ensureXCUITestDriver(config);
  process.stdout.write(`XCUITest driver ${XCUITEST_DRIVER_VERSION} 已就绪。\n`);
})().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
