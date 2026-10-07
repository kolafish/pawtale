const fs = require("node:fs/promises");
const path = require("node:path");
const { withTimeout } = require("./wait");

function safeTimestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function redactString(value, secrets) {
  return secrets.reduce(
    (result, secret) => (secret ? result.split(secret).join("[REDACTED]") : result),
    value,
  );
}

function redactValue(value, secrets, seen = new WeakSet()) {
  if (typeof value === "string") {
    return redactString(value, secrets);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  if (value instanceof Error) {
    return {
      name: value.name,
      message: redactString(value.message, secrets),
      stack: redactString(value.stack || "", secrets),
    };
  }
  if (seen.has(value)) {
    return "[CIRCULAR]";
  }
  seen.add(value);
  if (Array.isArray(value)) {
    return value.map((item) => redactValue(item, secrets, seen));
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      /(password|cookie|ticket|token|secret)/i.test(key)
        ? "[REDACTED]"
        : redactValue(item, secrets, seen),
    ]),
  );
}

async function createArtifactRecorder(config) {
  const runPath = path.join(config.artifactsPath, `foundation-${safeTimestamp()}`);
  const events = [];
  let failureCount = 0;
  const secrets = [config.account?.email, config.account?.password];
  await fs.mkdir(runPath, { recursive: true });

  return {
    events,
    runPath,
    mark(stage) {
      events.push({
        at: new Date().toISOString(),
        kind: "stage",
        value: stage,
      });
    },
    attach(miniProgram) {
      miniProgram.on("console", (message) => {
        events.push({
          at: new Date().toISOString(),
          kind: "console",
          value: redactValue(message, secrets),
        });
      });
      miniProgram.on("exception", (error) => {
        events.push({
          at: new Date().toISOString(),
          kind: "exception",
          value: redactValue(error, secrets),
        });
      });
    },
    async captureFailure(miniProgram, error) {
      failureCount += 1;
      const suffix = failureCount === 1 ? "" : `-${failureCount}`;
      let pageSnapshot = null;
      try {
        const page = await withTimeout(
          miniProgram.currentPage(),
          5000,
          "读取失败页面",
        );
        pageSnapshot = page
          ? {
              path: page.path,
              query: page.query,
              data: redactValue(
                await withTimeout(page.data(), 5000, "读取失败页面数据"),
                secrets,
              ),
            }
          : null;
        if (page && page.path !== "pages/login/index") {
          await withTimeout(
            miniProgram.screenshot({
              path: path.join(runPath, `failure${suffix}.png`),
            }),
            5000,
            "保存失败截图",
          );
        }
      } catch (captureError) {
        events.push({
          at: new Date().toISOString(),
          kind: "artifact-error",
          value: redactValue(captureError, secrets),
        });
      }
      await fs.writeFile(
        path.join(runPath, `failure${suffix}.json`),
        `${JSON.stringify({
          error: redactValue(error, secrets),
          page: pageSnapshot,
        }, null, 2)}\n`,
      );
    },
    async finish(summary) {
      await fs.writeFile(
        path.join(runPath, "run.json"),
        `${JSON.stringify(redactValue({ ...summary, events }, secrets), null, 2)}\n`,
      );
    },
  };
}

module.exports = {
  createArtifactRecorder,
  redactValue,
};
