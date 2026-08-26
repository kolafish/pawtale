const assert = require("node:assert/strict");
const http = require("node:http");
const { test } = require("node:test");
const { createIOSProjectFixture } = require("../support/ios-project-fixture");

function respond(response, statusCode, data, headers = {}) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    ...headers,
  });
  response.end(statusCode === 204 ? undefined : `${JSON.stringify(data)}\n`);
}

test("iOS project fixture captures only this run and restores the baseline exactly", async () => {
  const generatedTitle = "E2E-iOS-42-8";
  const requests = [];
  const state = {
    activeProjectId: "baseline-id",
    projects: [{ id: "baseline-id", title: generatedTitle }],
  };
  const server = http.createServer((request, response) => {
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
      && url.pathname === "/api/mobile/memoir/projects/created-id"
    ) {
      state.projects = state.projects.filter((project) => project.id !== "created-id");
      respond(response, 204, null);
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
    const fixture = await createIOSProjectFixture({
      account: { email: "fixture@example.invalid", password: "fixture-password" },
      httpBaseUrl: `http://127.0.0.1:${address.port}`,
    }, {
      now: () => 42,
      random: () => 0.5,
    });
    assert.equal(fixture.initialTitle, generatedTitle);
    state.projects.push({
      id: "created-id",
      title: generatedTitle,
      narrator_age: 72,
    });
    state.activeProjectId = "created-id";
    const created = await fixture.captureCreatedProject(generatedTitle);
    assert.equal(created.id, "created-id");
    state.projects.find((project) => project.id === "created-id").title = fixture.renamedTitle;
    await fixture.assertProject("created-id", fixture.renamedTitle, true, {
      narrator_age: 72,
    });
    await fixture.cleanup();
    await fixture.cleanup();
  } finally {
    await new Promise((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  }
  assert.deepEqual(state.projects, [{ id: "baseline-id", title: generatedTitle }]);
  assert.equal(state.activeProjectId, "baseline-id");
  assert.equal(requests.filter((request) => request.startsWith("DELETE ")).length, 1);
});

test("iOS project fixture refuses an ambiguous run title", async () => {
  const responses = [
    {
      active_project_id: "baseline-id",
      projects: [{ id: "baseline-id", title: "baseline" }],
    },
    {
      active_project_id: "created-a",
      projects: [
        { id: "baseline-id", title: "baseline" },
        { id: "created-a", title: "E2E-iOS-42-8" },
        { id: "created-b", title: "E2E-iOS-42-8" },
      ],
    },
  ];
  const fixture = await createIOSProjectFixture({
    account: { email: "fixture@example.invalid", password: "fixture-password" },
    httpBaseUrl: "https://api.example.invalid",
  }, {
    now: () => 42,
    random: () => 0.5,
    fetch: async (url) => {
      if (url.endsWith("/api/auth/login")) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => "pawtale_session=fixture; Path=/" },
        };
      }
      const value = responses.shift();
      return {
        ok: true,
        status: 200,
        async json() { return value; },
      };
    },
  });
  await assert.rejects(
    fixture.captureCreatedProject(fixture.initialTitle),
    /匹配 2 本/,
  );
});
