const fs = require("node:fs/promises");
const path = require("node:path");
const { redactValue } = require("./artifacts");
const { isPawtaleSource } = require("./ios-ui-smoke");

function redactSource(source, secrets) {
  return secrets.reduce(
    (value, secret) => (secret ? value.split(secret).join("[REDACTED]") : value),
    String(source || ""),
  );
}

async function createIOSUIArtifacts(config, runPath) {
  const events = [];
  const secrets = [config.account.email, config.account.password];
  await fs.mkdir(runPath, { recursive: true });
  return {
    events,
    mark(stage) {
      events.push({
        at: new Date().toISOString(),
        kind: "stage",
        value: stage,
      });
    },
    async screenshot(client, fileName, source = "") {
      if (
        !isPawtaleSource(source)
        || source.includes("欢迎登录")
        || source.includes(config.account.email)
      ) {
        return;
      }
      await client.screenshot(path.join(runPath, fileName));
    },
    async captureFailure(client, error) {
      let source = "";
      try {
        source = await client.source();
        if (isPawtaleSource(source)) {
          await fs.writeFile(
            path.join(runPath, "failure-source.xml"),
            redactSource(source, secrets),
          );
        } else {
          events.push({
            at: new Date().toISOString(),
            kind: "artifact-skipped",
            value: "failure UI was not a verified Pawtale page",
          });
        }
      } catch (captureError) {
        events.push({
          at: new Date().toISOString(),
          kind: "artifact-error",
          value: redactValue(captureError, secrets),
        });
      }
      if (
        isPawtaleSource(source)
        && !source.includes("欢迎登录")
        && !source.includes(config.account.email)
      ) {
        try {
          await client.screenshot(path.join(runPath, "failure.png"));
        } catch (captureError) {
          events.push({
            at: new Date().toISOString(),
            kind: "artifact-error",
            value: redactValue(captureError, secrets),
          });
        }
      }
      await fs.writeFile(
        path.join(runPath, "failure.json"),
        `${JSON.stringify(redactValue(error, secrets), null, 2)}\n`,
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
  createIOSUIArtifacts,
  redactSource,
};
