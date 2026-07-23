const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")

const projectConfigs = [
  path.resolve(__dirname, "../../project.config.json"),
  path.resolve(__dirname, "../project.config.json"),
]

test("all WeChat project entry points keep SWC disabled", () => {
  for (const configPath of projectConfigs) {
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"))

    assert.equal(
      config.setting?.swc,
      false,
      `${configPath} must explicitly disable SWC`,
    )
  }
})
