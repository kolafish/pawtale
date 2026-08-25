const assert = require("node:assert/strict");
const http = require("node:http");
const { test } = require("node:test");
const { cleanupProjectsThroughApi } = require("../support/session");

function respond(response, statusCode, data, headers = {}) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    ...headers,
  });
  response.end(`${JSON.stringify(data)}\n`);
}

test("API cleanup restores the baseline and deletes an exact pending title", async () => {
  const requests = [];
  const state = {
    activeProjectId: "e2e-id",
    projects: [
      { id: "e2e-id", title: "E2E-unique-run" },
      { id: "baseline-id", title: "我的回忆录" },
    ],
  };
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    requests.push(`${request.method} ${url.pathname}`);
    if (request.method === "POST" && url.pathname === "/api/auth/login") {
      respond(response, 200, { user: { id: "e2e-user" } }, {
        "set-cookie": "pawtale_session=fixture; HttpOnly; Path=/",
      });
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/mobile/memoir/projects") {
      respond(response, 200, {
        active_project_id: state.activeProjectId,
        projects: state.projects,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mobile/memoir/projects/baseline-id/activate"
    ) {
      state.activeProjectId = "baseline-id";
      respond(response, 200, {
        active_project_id: state.activeProjectId,
        projects: state.projects,
      });
      return;
    }
    if (
      request.method === "DELETE"
      && url.pathname === "/api/mobile/memoir/projects/e2e-id"
    ) {
      state.projects = state.projects.filter((project) => project.id !== "e2e-id");
      respond(response, 204, {});
      return;
    }
    respond(response, 404, { detail: "not found" });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  try {
    const address = server.address();
    assert.equal(typeof address, "object");
    await cleanupProjectsThroughApi({
      config: {
        account: { email: "fixture@example.invalid", password: "fixture-password" },
        httpBaseUrl: `http://127.0.0.1:${address.port}`,
      },
      createdProjectIds: [],
      createdProjectTitles: ["E2E-unique-run"],
      initialActiveProjectId: "baseline-id",
    });
  } finally {
    await new Promise((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  }
  assert.deepEqual(state.projects, [{ id: "baseline-id", title: "我的回忆录" }]);
  assert.equal(state.activeProjectId, "baseline-id");
  assert.deepEqual(requests, [
    "POST /api/auth/login",
    "GET /api/mobile/memoir/projects",
    "POST /api/mobile/memoir/projects/baseline-id/activate",
    "DELETE /api/mobile/memoir/projects/e2e-id",
    "GET /api/mobile/memoir/projects",
  ]);
});
