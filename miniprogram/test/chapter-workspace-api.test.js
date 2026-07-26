require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");


function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-book-completion-api.ts",
    "../services/memoir-chapter-workspace-api.ts",
  ]) {
    delete require.cache[require.resolve(modulePath)];
  }
}

function installWx(requests) {
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-flash" };
      }
      return "";
    },
    getExtConfigSync() { return {}; },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: {}, cookies: [], header: {} });
    },
  };
}

test("chapter completion API exposes workspace, section AI revision, prepare, and rewrite actions", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    cancelMemoirChapterSectionRegenerationJob,
    getMemoirChapterWorkspace,
    getMemoirChapterSectionRegenerationJob,
    moveMemoirChapterMaterial,
    placeMemoirChapterMaterial,
    reviseMemoirChapterDraftSectionWithAI,
    regenerateMemoirChapterSections,
  } = require("../services/memoir-chapter-workspace-api.ts");
  const {
    cancelMemoirChapterPreparationJob,
    enqueueMemoirChapterPreparationJob,
    getMemoirChapterPreparationJob,
  } = require("../services/memoir-book-completion-api.ts");

  await getMemoirChapterWorkspace("project/1", "chapter/1");
  await regenerateMemoirChapterSections(
    "project/1",
    "chapter/1",
    [{ id: "section/1", version: 3 }],
  );
  await getMemoirChapterSectionRegenerationJob("project/1", "chapter/1");
  await cancelMemoirChapterSectionRegenerationJob(
    "project/1",
    "chapter/1",
    "job/1",
  );
  await reviseMemoirChapterDraftSectionWithAI(
    "project/1",
    "chapter/1",
    "section/1",
    "version/1",
    "写得更平实一些",
  );
  await moveMemoirChapterMaterial("project/1", "utterance/1", "section/2");
  await placeMemoirChapterMaterial("project/1", "utterance/1", {
    target_chapter_id: "chapter/1",
    new_section_title: "新的小节",
  });
  await enqueueMemoirChapterPreparationJob(
    "project/1",
    "chapter/1",
    "rewrite",
  );
  await getMemoirChapterPreparationJob("project/1", "chapter/1");
  await cancelMemoirChapterPreparationJob(
    "project/1",
    "chapter/1",
    "job/2",
  );

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/workspace");
  assert.equal(apiRequests[0].timeout, 30000);
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/section-regeneration-jobs");
  assert.deepEqual(apiRequests[1].data, {
    expected_sections: [{ id: "section/1", version: 3 }],
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[1].timeout, 60000);
  assert.equal(apiRequests[2].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/section-regeneration-jobs/current");
  assert.equal(apiRequests[2].method, "GET");
  assert.equal(apiRequests[3].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/section-regeneration-jobs/job%2F1");
  assert.equal(apiRequests[3].method, "DELETE");
  assert.equal(apiRequests[4].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-sections/section%2F1/ai-revise");
  assert.deepEqual(apiRequests[4].data, {
    expected_version_id: "version/1",
    instruction_text: "写得更平实一些",
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[4].timeout, 600000);
  assert.equal(apiRequests[5].url, "http://backend.local:8000/api/memoir/projects/project%2F1/section-materials/utterance%2F1/move");
  assert.deepEqual(apiRequests[5].data, { target_section_id: "section/2" });
  assert.equal(apiRequests[5].timeout, 60000);
  assert.equal(apiRequests[6].url, "http://backend.local:8000/api/memoir/projects/project%2F1/section-materials/utterance%2F1/place");
  assert.deepEqual(apiRequests[6].data, {
    target_chapter_id: "chapter/1",
    new_section_title: "新的小节",
  });
  assert.equal(apiRequests[6].timeout, 60000);
  assert.equal(apiRequests[7].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/jobs");
  assert.deepEqual(apiRequests[7].data, {
    operation: "rewrite",
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[7].timeout, 60000);
  assert.equal(apiRequests[8].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/jobs/current");
  assert.equal(apiRequests[8].method, "GET");
  assert.equal(apiRequests[8].timeout, 30000);
  assert.equal(apiRequests[9].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/jobs/job%2F2");
  assert.equal(apiRequests[9].method, "DELETE");
  assert.equal(apiRequests[9].timeout, 60000);
  assert.equal(apiRequests.length, 10);
  assert.equal(requests.length, 10);
  delete global.wx;
});
