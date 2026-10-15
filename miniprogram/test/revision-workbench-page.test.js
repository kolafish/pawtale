require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  mockMemoirApiModules,
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");

function revisionProposal(overrides = {}) {
  const beforeSection = {
    section_id: "section-1",
    title: "旧校舍",
    content: "修改前正文",
  };
  const afterSection = { ...beforeSection, content: "修改后正文" };
  const chapter = (section) => ({
    chapter_id: "chapter-1",
    title: "上学时光",
    content: section.content,
    sections: [section],
  });
  return {
    id: "proposal-1",
    project_id: "project-1",
    status: "ready",
    phase: "draft_ready",
    revision_plan: null,
    summary: "修改预览已准备。",
    base_output_version_id: "version-1",
    result_output_version_id: null,
    comparison: {
      changed_chapter_count: 1,
      changed_section_count: 1,
      before_char_count: 5,
      after_char_count: 5,
      char_count_delta: 0,
      chapter_order_changed: false,
      before_chapter_order: [{ chapter_id: "chapter-1", title: "上学时光" }],
      after_chapter_order: [{ chapter_id: "chapter-1", title: "上学时光" }],
      chapters: [{
        chapter_id: "chapter-1",
        title: "上学时光",
        sections: [{
          section_id: "section-1",
          title: "旧校舍",
          before_title: "旧校舍",
          after_title: "校园旧事",
          before_content: "修改前正文",
          after_content: "修改后正文",
          before_char_count: 5,
          after_char_count: 5,
        }],
      }],
      metadata_changes: [],
    },
    before: {
      version: 1,
      manuscript: { chapters: [chapter(beforeSection)] },
    },
    after: {
      version: 2,
      manuscript: { chapters: [chapter(afterSection)] },
    },
    can_apply: true,
    can_dismiss: true,
    created_at: "2026-07-30T00:00:00",
    updated_at: "2026-07-30T00:00:00",
    ...overrides,
  };
}

function revisionPlanProposal(overrides = {}) {
  return revisionProposalMetadata({
    phase: "plan_ready",
    revision_plan: {
      summary: "保留两段经历，并统一人物称谓。",
      items: [{
        title: "统一人物称谓",
        reason: "同一人物在不同章节中的称呼不一致。",
        approach: "保留两段经历，只统一相关人物的称谓。",
        targets: ["上学时光", "旧校舍"],
      }],
      book_metadata_patch: {},
    },
    summary: "保留两段经历，并统一人物称谓。",
    comparison: {
      changed_chapter_count: 0,
      changed_section_count: 0,
      before_char_count: 5,
      after_char_count: 5,
      char_count_delta: 0,
      chapter_order_changed: false,
      before_chapter_order: [{ chapter_id: "chapter-1", title: "上学时光" }],
      after_chapter_order: [{ chapter_id: "chapter-1", title: "上学时光" }],
      chapters: [],
      metadata_changes: [],
    },
    can_apply: false,
    ...overrides,
  });
}

function revisionProposalMetadata(overrides = {}) {
  const proposal = revisionProposal(overrides);
  return {
    ...proposal,
    before: { ...proposal.before, manuscript: null },
    after: { ...proposal.after, manuscript: null },
  };
}

function revisionProposalPreview(version, overrides = {}) {
  const proposal = revisionProposal(overrides);
  const other = version === "before" ? "after" : "before";
  return {
    ...proposal,
    [other]: { ...proposal[other], manuscript: null },
  };
}

function revisionFeedbackJob(status = "idle") {
  return {
    job_id: status === "idle" ? null : "feedback-job-1",
    project_id: "project-1",
    status,
    stage: status === "succeeded" ? "completed" : (
      status === "idle" ? "" : "queued"
    ),
    proposal_id: "proposal-1",
    proposal_status: "ready",
    proposal_phase: "plan_ready",
    requested_ai_model: null,
    resolved_ai_model: null,
    can_cancel: status === "pending" || status === "running",
    can_retry: status === "failed",
    error_message: "",
  };
}

function loadRevisionPage({ api = {}, handedOffFeedback = "" } = {}) {
  mockMemoirApiModules({
    getMemoirBookRevisionProposal: async (_projectId, _proposalId, preview = "none") => (
      preview === "none"
        ? revisionProposalMetadata()
        : revisionProposalPreview(preview)
    ),
    enqueueMemoirBookRevisionFeedback: async () => revisionFeedbackJob("pending"),
    getMemoirBookRevisionFeedbackJob: async () => revisionFeedbackJob(),
    enqueueMemoirBookRevisionDraft: async () => revisionFeedbackJob("pending"),
    getMemoirBookRevisionDraftJob: async () => revisionFeedbackJob(),
    applyMemoirBookRevisionProposal: async () => revisionProposalMetadata({
      status: "applied",
      can_apply: false,
      can_dismiss: false,
      result_output_version_id: "version-2",
    }),
    dismissMemoirBookRevisionProposal: async () => revisionProposalMetadata({
      status: "dismissed",
      can_apply: false,
      can_dismiss: false,
    }),
    enqueueMemoirChapterSectionRevision: async () => revisionFeedbackJob("pending"),
    getMemoirChapterSectionRevisionJob: async () => revisionFeedbackJob(),
    getMemoirChapterSectionRevisionProposal: async () => revisionProposalMetadata(),
    applyMemoirChapterSectionRevision: async () => revisionProposalMetadata({
      status: "applied",
      can_apply: false,
      can_dismiss: false,
      result_output_version_id: "version-2",
    }),
    dismissMemoirChapterSectionRevision: async () => revisionProposalMetadata({
      status: "dismissed",
      can_apply: false,
      can_dismiss: false,
    }),
    ...api,
  });
  mockModule("../services/memoir-api-client.ts", {
    memoirProjectDictationUrl: () =>
      "ws://backend.local/api/memoir/projects/project-1/dictation",
  });
  mockModule("../services/navigation.ts", {
    routeAuthError: () => false,
    consumeSectionRevisionFeedback: () => handedOffFeedback,
  });
  return loadPageModule("../pages/revision-workbench/index.ts");
}

test("section revision resolves an existing proposal before mounting its voice editor", async () => {
  let resolveJob;
  const job = new Promise((resolve) => {
    resolveJob = resolve;
  });
  global.wx = { setNavigationBarTitle() {} };
  const { page } = loadRevisionPage({
    api: {
      getMemoirChapterSectionRevisionJob: async () => job,
      getMemoirChapterSectionRevisionProposal: async () => revisionPlanProposal(),
    },
  });
  const context = pageContext(page);

  const loading = page.onLoad.call(context, {
    scope: "chapter_section",
    projectId: "project-1",
    chapterId: "chapter-1",
    sectionId: "section-1",
    sectionTitle: "旧校舍",
  });
  await Promise.resolve();

  assert.equal(context.data.loading, true);
  assert.equal(context.data.proposal, null);

  resolveJob({
    ...revisionFeedbackJob("succeeded"),
    operation: "plan",
    proposal_id: "proposal-1",
  });
  await loading;

  assert.equal(context.data.loading, false);
  assert.equal(context.data.proposal.phase, "plan_ready");
  assert.equal(context.data.proposalId, "proposal-1");
});

test("section revision keeps a request handed over from voice edit while restoring an older plan", async () => {
  global.wx = { setNavigationBarTitle() {} };
  const { page } = loadRevisionPage({
    handedOffFeedback: "把奶奶的部分写得更细",
    api: {
      getMemoirChapterSectionRevisionJob: async () => ({
        ...revisionFeedbackJob("succeeded"), operation: "plan", proposal_id: "proposal-1",
      }),
      getMemoirChapterSectionRevisionProposal: async () => revisionPlanProposal(),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    scope: "chapter_section", projectId: "project-1", chapterId: "chapter-1", sectionId: "section-1",
  });
  assert.equal(context.data.proposalId, "proposal-1");
  assert.equal(context.data.feedbackText, "把奶奶的部分写得更细");
});

test("section revision keeps a handed-over request when an older job finishes later", async () => {
  global.wx = { setNavigationBarTitle() {}, showToast() {} };
  let status = "running";
  let scheduledFeedbackPoll = null;
  const { page } = loadRevisionPage({
    handedOffFeedback: "把奶奶的部分写得更细",
    api: {
      getMemoirChapterSectionRevisionJob: async () => ({
        ...revisionFeedbackJob(status), operation: "revise", proposal_id: "proposal-1",
      }),
      getMemoirChapterSectionRevisionProposal: async () => revisionPlanProposal(),
    },
  });
  const context = pageContext(page);
  context.pageActive = true;
  await page.onLoad.call(context, {
    scope: "chapter_section", projectId: "project-1", chapterId: "chapter-1", sectionId: "section-1",
  });
  context.feedbackPoller = {
    cancel() {},
    schedule(callback) {
      scheduledFeedbackPoll = callback;
      return true;
    },
  };
  await page.refreshFeedbackJob.call(context);
  assert.equal(context.data.feedbackBusy, true);
  assert.ok(scheduledFeedbackPoll);
  status = "succeeded";
  scheduledFeedbackPoll();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(context.data.feedbackBusy, false);
  assert.equal(context.data.feedbackText, "把奶奶的部分写得更细");
});

test("book revision plan presents proposed book information before drafting", () => {
  const { mod } = loadRevisionPage();
  const proposal = revisionPlanProposal({
    revision_plan: {
      summary: "让书名更贴近故园主线。",
      items: [],
      book_metadata_patch: {
        title: "风从故园来",
        subtitle: "一部家庭口述回忆录",
      },
    },
  });

  assert.deepEqual(
    mod.revisionViewPatch(proposal).metadataChanges.map(
      ({ label, display_value }) => [label, display_value],
    ),
    [
      ["书名", "风从故园来"],
      ["副标题", "一部家庭口述回忆录"],
    ],
  );
});

test("book revision preview exposes before and after book information", () => {
  const { mod } = loadRevisionPage();
  const proposal = revisionProposalMetadata({
    comparison: {
      ...revisionProposal().comparison,
      changed_chapter_count: 0,
      changed_section_count: 0,
      chapters: [],
      metadata_changes: [{
        field: "back_cover_quote",
        before_value: "旧封底文案",
        after_value: "新封底文案",
      }],
    },
  });

  assert.deepEqual(mod.revisionViewPatch(proposal).metadataChanges, [{
    field: "back_cover_quote",
    label: "封底文案",
    before_value: "旧封底文案",
    after_value: "新封底文案",
    display_value: "新封底文案",
  }]);
});

test("book revision page loads only the selected whole-book preview on demand", async () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/revision-workbench/index"));

  const previewRequests = [];
  const { mod, page } = loadRevisionPage({
    api: {
      getMemoirBookRevisionProposal: async (_projectId, _proposalId, preview = "none") => {
        previewRequests.push(preview);
        return preview === "none"
          ? revisionProposalMetadata()
          : revisionProposalPreview(preview, preview === "before"
            ? { summary: "修改预览已更新。", updated_at: "2026-07-30T00:01:00" }
            : {});
      },
    },
  });
  const { revisionViewPatch } = mod;
  const proposal = revisionProposalMetadata();
  const after = revisionViewPatch(proposal);
  const before = revisionViewPatch(proposal, "before");

  assert.equal(after.changedChapters[0].sections[0].display_content, "修改后正文");
  assert.equal(before.changedChapters[0].sections[0].display_content, "修改前正文");
  assert.equal(after.changedChapters[0].sections[0].display_title, "校园旧事");
  assert.equal(before.changedChapters[0].sections[0].display_title, "旧校舍");
  assert.equal(after.proposal.comparison.before_chapter_order[0].title, "上学时光");
  assert.equal("before" in after.proposal, false);
  assert.equal("chapters" in after.proposal.comparison, false);
  assert.equal("wholeChapters" in after, false);

  const context = pageContext(page, {
    projectId: "project-1",
    proposalId: "proposal-1",
    proposal,
  });
  context.revisionProposal = proposal;
  await page.switchMode.call(context, {
    currentTarget: { dataset: { mode: "whole" } },
  });
  assert.deepEqual(previewRequests, ["after"]);
  assert.equal(context.data.wholeChapters[0].sections[0].content, "修改后正文");

  await page.switchPreviewVersion.call(context, {
    currentTarget: { dataset: { version: "before" } },
  });
  assert.deepEqual(previewRequests, ["after", "before"]);
  assert.equal(context.data.wholeChapters[0].sections[0].content, "修改前正文");
  assert.equal(context.data.proposal.summary, "修改预览已更新。");
});

test("book revision page confirms or dismisses the candidate without hidden actions", async () => {
  const actions = [];
  global.wx = {
    showModal(options) {
      options.success({ confirm: true, cancel: false });
    },
    showToast() {},
    navigateBack() {
      actions.push("back");
    },
  };
  const { page } = loadRevisionPage({
    api: {
      applyMemoirBookRevisionProposal: async (projectId, proposalId) => {
        actions.push(["apply", projectId, proposalId]);
        return revisionProposalMetadata({
          status: "applied",
          can_apply: false,
          can_dismiss: false,
          result_output_version_id: "version-2",
        });
      },
      dismissMemoirBookRevisionProposal: async (projectId, proposalId) => {
        actions.push(["dismiss", projectId, proposalId]);
        return revisionProposalMetadata({ status: "dismissed" });
      },
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    proposalId: "proposal-1",
    proposal: revisionProposalMetadata(),
  });

  await page.applyRevision.call(context);
  context.data.proposal = revisionProposalMetadata();
  context.data.busy = false;
  await page.dismissRevision.call(context);

  assert.deepEqual(actions, [
    ["apply", "project-1", "proposal-1"],
    ["dismiss", "project-1", "proposal-1"],
    "back",
  ]);
});

test("book revision feedback refreshes the whole plan before drafting", async () => {
  const calls = [];
  const proposal = revisionPlanProposal();
  global.wx = {
    showToast(options) {
      calls.push(["toast", options.title]);
    },
  };
  const { page } = loadRevisionPage({
    api: {
      enqueueMemoirBookRevisionFeedback: async (
        projectId,
        proposalId,
        feedback,
      ) => {
        calls.push(["feedback", projectId, proposalId, feedback]);
        return revisionFeedbackJob("pending");
      },
      getMemoirBookRevisionFeedbackJob: async () =>
        revisionFeedbackJob("succeeded"),
      getMemoirBookRevisionProposal: async () => {
        calls.push(["reload"]);
        return revisionPlanProposal({
          summary: "已更新整体修改思路。",
          revision_plan: {
            summary: "已更新整体修改思路。",
            items: [{
              title: "统一称谓并精简重复",
              reason: "保留经历的同时避免重复。",
              approach: "统一称谓，并压缩重复叙述。",
              targets: ["上学时光"],
            }],
          },
          updated_at: "2026-07-30T00:01:00",
        });
      },
    },
  });
  let pollScheduled = false;
  const context = pageContext(page, {
    projectId: "project-1",
    proposalId: "proposal-1",
    proposal,
    feedbackText: "请统一前后章节的人物称谓。",
  });
  context.pageActive = true;
  context.feedbackPoller = {
    cancel() {},
    schedule() {
      pollScheduled = true;
      return true;
    },
  };

  await page.submitFeedback.call(context);
  assert.equal(context.data.feedbackBusy, true);
  assert.equal(pollScheduled, true);
  await page.refreshFeedbackJob.call(context);

  assert.deepEqual(calls[0], [
    "feedback",
    "project-1",
    "proposal-1",
    "请统一前后章节的人物称谓。",
  ]);
  assert.equal(context.data.feedbackBusy, false);
  assert.equal(context.data.feedbackText, "");
  assert.equal(context.data.proposal.summary, "已更新整体修改思路。");
  assert.equal(
    context.data.proposal.revision_plan.items[0].approach,
    "统一称谓，并压缩重复叙述。",
  );
  assert.ok(calls.some(([kind]) => kind === "reload"));
});

test("book revision generates a draft only after the user confirms the plan", async () => {
  const calls = [];
  const { page } = loadRevisionPage({
    api: {
      enqueueMemoirBookRevisionDraft: async (projectId, proposalId) => {
        calls.push(["draft", projectId, proposalId]);
        return revisionFeedbackJob("pending");
      },
      getMemoirBookRevisionDraftJob: async () =>
        revisionFeedbackJob("succeeded"),
      getMemoirBookRevisionProposal: async () => revisionProposalMetadata({
        summary: "修改稿已生成。",
        updated_at: "2026-07-30T00:02:00",
      }),
    },
  });
  let pollScheduled = false;
  const context = pageContext(page, {
    projectId: "project-1",
    proposalId: "proposal-1",
    proposal: revisionPlanProposal(),
  });
  context.pageActive = true;
  context.draftPoller = {
    cancel() {},
    schedule() {
      pollScheduled = true;
      return true;
    },
  };

  await page.generateDraft.call(context);
  assert.equal(context.data.draftBusy, true);
  assert.equal(pollScheduled, true);
  await page.refreshDraftJob.call(context);

  assert.deepEqual(calls[0], ["draft", "project-1", "proposal-1"]);
  assert.equal(context.data.draftBusy, false);
  assert.equal(context.data.proposal.phase, "draft_ready");
  assert.equal(context.data.proposal.summary, "修改稿已生成。");
});

test("book revision keeps submit error when polling only finds an older success", async () => {
  global.wx = { showToast() {} };
  const { page } = loadRevisionPage({
    api: {
      enqueueMemoirBookRevisionFeedback: async () => {
        throw new Error("network unavailable");
      },
      getMemoirBookRevisionFeedbackJob: async () =>
        revisionFeedbackJob("succeeded"),
      getMemoirBookRevisionProposal: async () => revisionProposalMetadata(),
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    proposalId: "proposal-1",
    proposal: revisionProposalMetadata(),
    feedbackText: "请统一前后章节的人物称谓。",
  });
  context.pageActive = true;
  context.feedbackPoller = { cancel() {}, schedule() { return true; } };

  await page.submitFeedback.call(context);
  await page.refreshFeedbackJob.call(context);

  assert.equal(context.data.feedbackBusy, false);
  assert.equal(context.data.feedbackText, "请统一前后章节的人物称谓。");
  assert.equal(context.data.error, "network unavailable");
});

test("direct section preview generates, refines, and saves with one confirmation", async () => {
  const calls = [];
  let previewReady = false;
  let scheduledFeedbackPoll = null;
  global.wx = {
    setNavigationBarTitle() {},
    showToast() {},
    showModal(options) {
      assert.fail("A section preview must not ask for confirmation twice");
    },
    navigateBack() {},
  };
  const { page } = loadRevisionPage({
    api: {
      getMemoirChapterSectionRevisionJob: async () => ({
        ...revisionFeedbackJob(previewReady ? "succeeded" : "idle"),
        operation: "revise",
        proposal_id: previewReady ? "proposal-1" : null,
      }),
      enqueueMemoirChapterSectionRevision: async (...args) => {
        calls.push(args);
        if (!args[4]) {
          previewReady = true;
          return revisionFeedbackJob("pending");
        }
        return revisionFeedbackJob("succeeded");
      },
      getMemoirChapterSectionRevisionProposal: async () => (
        revisionProposalMetadata()
      ),
      applyMemoirChapterSectionRevision: async (...args) => {
        calls.push(["apply", ...args]);
        return revisionProposalMetadata({
          status: "applied",
          can_apply: false,
          can_dismiss: false,
          result_output_version_id: "version-2",
        });
      },
    },
  });
  const context = pageContext(page);
  context.pageActive = true;
  await page.onLoad.call(context, {
    scope: "chapter_section",
    projectId: "project-1",
    chapterId: "chapter-1",
    sectionId: "section-1",
    sectionTitle: "旧校舍",
  });
  context.feedbackPoller = {
    cancel() {},
    schedule(callback) {
      scheduledFeedbackPoll = callback;
      return true;
    },
  };

  await page.submitFeedback.call(context, {
    detail: { value: "保留事实，把语气写得更平实。" },
  });
  assert.equal(context.data.feedbackBusy, true);
  assert.ok(scheduledFeedbackPoll);
  scheduledFeedbackPoll();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(context.data.proposal.phase, "draft_ready");
  await page.submitFeedback.call(context, { detail: { value: "把最后一句再精简一点。" } });
  assert.equal(context.data.proposal.phase, "draft_ready");
  assert.equal(
    context.data.changedChapters[0].sections[0].display_content,
    "修改后正文",
  );
  await page.applyRevision.call(context);

  assert.deepEqual(calls, [
    [
      "project-1",
      "chapter-1",
      "section-1",
      "revise",
      null,
      "保留事实，把语气写得更平实。",
    ],
    [
      "project-1",
      "chapter-1",
      "section-1",
      "revise",
      "proposal-1",
      "把最后一句再精简一点。",
    ],
    [
      "apply",
      "project-1",
      "chapter-1",
      "section-1",
      "proposal-1",
    ],
  ]);
});

for (const status of ["running", "failed", "succeeded"]) {
  test(`direct section preview restores ${status} work without submitting again`, async () => {
    global.wx = { setNavigationBarTitle() {}, showToast() {} };
    let enqueued = 0;
    const { page } = loadRevisionPage({ api: {
      enqueueMemoirChapterSectionRevision: async () => { enqueued += 1; },
      getMemoirChapterSectionRevisionJob: async () => ({
        ...revisionFeedbackJob(status), operation: "revise", proposal_id: "proposal-1",
        error_message: status === "failed" ? "本次生成失败，预览仍保留" : "",
      }),
      getMemoirChapterSectionRevisionProposal: async () => revisionProposalMetadata({
        can_apply: status === "succeeded", can_dismiss: status !== "running",
      }),
    } });
    const context = pageContext(page);
    await page.onLoad.call(context, {
      scope: "chapter_section", projectId: "project-1", chapterId: "chapter-1", sectionId: "section-1",
    });
    assert.equal(context.data.proposal.phase, "draft_ready");
    assert.equal(context.data.proposalId, "proposal-1");
    assert.equal(context.data.feedbackBusy, status === "running");
    assert.equal(context.data.draftBusy, false);
    assert.equal(context.data.proposal.can_apply, status === "succeeded");
    assert.equal(context.data.changedChapters[0].sections[0].display_content, "修改后正文");
    if (status === "failed") assert.match(context.data.error, /本次生成失败/);
    assert.equal(enqueued, 0);
    page.onUnload.call(context);
  });
}

for (const bodyChanged of [false, true]) {
  test(`metadata preview confirmation preserves body guard: ${bodyChanged}`, async () => {
    let modals = 0;
    let applied = 0;
    global.wx = {
      setNavigationBarTitle() {}, showToast() {},
      showModal(options) { modals += 1; options.success({ confirm: true }); },
    };
    const original = revisionProposalMetadata();
    const preview = revisionProposalMetadata({
      comparison: {
        ...original.comparison,
        changed_chapter_count: bodyChanged ? 1 : 0,
        changed_section_count: bodyChanged ? 1 : 0,
        chapters: bodyChanged ? original.comparison.chapters : [],
        metadata_changes: [{ field: "narrator_name", before_value: "老余", after_value: "余建国" }],
      },
    });
    const { page } = loadRevisionPage({ api: {
      getMemoirBookRevisionProposal: async () => preview,
      applyMemoirBookRevisionProposal: async () => {
        applied += 1;
        return { ...preview, status: "applied", can_apply: false, can_dismiss: false };
      },
    } });
    const context = pageContext(page);
    await page.onLoad.call(context, { projectId: "project-1", proposalId: "proposal-1" });
    await page.applyRevision.call(context);
    assert.equal(applied, 1);
    assert.equal(modals, bodyChanged ? 1 : 0);
    assert.equal(context.data.proposal.status, "applied");
  });
}
