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

test("chapter completion API exposes workspace, section revision workbench, prepare, and rewrite actions", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    cancelMemoirChapterSectionRegenerationJob,
    applyMemoirChapterSectionRevision,
    dismissMemoirChapterSectionRevision,
    enqueueMemoirChapterSectionRevision,
    getMemoirChapterWorkspace,
    getMemoirChapterSectionRevisionJob,
    getMemoirChapterSectionRevisionProposal,
    getMemoirChapterSectionRegenerationJob,
    placeMemoirChapterMaterial,
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
  await enqueueMemoirChapterSectionRevision(
    "project/1",
    "chapter/1",
    "section/1",
    "plan",
    null,
    "写得更平实一些",
  );
  await getMemoirChapterSectionRevisionJob(
    "project/1", "chapter/1", "section/1",
  );
  await getMemoirChapterSectionRevisionProposal(
    "project/1", "chapter/1", "section/1", "proposal/1",
  );
  await applyMemoirChapterSectionRevision(
    "project/1", "chapter/1", "section/1", "proposal/1",
  );
  await dismissMemoirChapterSectionRevision(
    "project/1", "chapter/1", "section/1", "proposal/1",
  );
  await placeMemoirChapterMaterial("project/1", "utterance/1", { target_section_id: "section/2" });
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
  assert.equal(apiRequests[4].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-sections/section%2F1/revision/jobs");
  assert.deepEqual(apiRequests[4].data, {
    operation: "plan",
    proposal_id: null,
    feedback: "写得更平实一些",
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[4].timeout, 60000);
  assert.equal(apiRequests[5].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-sections/section%2F1/revision/jobs/current");
  assert.equal(apiRequests[5].method, "GET");
  assert.equal(apiRequests[6].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-sections/section%2F1/revision/proposals/proposal%2F1");
  assert.equal(apiRequests[6].method, "GET");
  assert.equal(apiRequests[7].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-sections/section%2F1/revision/proposals/proposal%2F1/apply");
  assert.equal(apiRequests[7].method, "POST");
  assert.equal(apiRequests[8].url, "http://backend.local:8000/api/memoir/projects/project%2F1/chapters/chapter%2F1/draft-sections/section%2F1/revision/proposals/proposal%2F1/dismiss");
  assert.equal(apiRequests[8].method, "POST");
  assert.equal(apiRequests[9].url, "http://backend.local:8000/api/memoir/projects/project%2F1/section-materials/utterance%2F1/place");
  assert.deepEqual(apiRequests[9].data, { target_section_id: "section/2" });
  assert.equal(apiRequests[9].timeout, 60000);
  assert.equal(apiRequests[10].url, "http://backend.local:8000/api/memoir/projects/project%2F1/section-materials/utterance%2F1/place");
  assert.deepEqual(apiRequests[10].data, {
    target_chapter_id: "chapter/1",
    new_section_title: "新的小节",
  });
  assert.equal(apiRequests[10].timeout, 60000);
  assert.equal(apiRequests[11].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/jobs");
  assert.deepEqual(apiRequests[11].data, {
    operation: "rewrite",
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(apiRequests[11].timeout, 60000);
  assert.equal(apiRequests[12].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/jobs/current");
  assert.equal(apiRequests[12].method, "GET");
  assert.equal(apiRequests[12].timeout, 30000);
  assert.equal(apiRequests[13].url, "http://backend.local:8000/api/memoir/projects/project%2F1/book-completion/chapters/chapter%2F1/jobs/job%2F2");
  assert.equal(apiRequests[13].method, "DELETE");
  assert.equal(apiRequests[13].timeout, 60000);
  assert.equal(apiRequests.length, 14);
  assert.equal(requests.length, 14);
  delete global.wx;
});
