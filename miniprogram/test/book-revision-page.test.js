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
          before_content: "修改前正文",
          after_content: "修改后正文",
          before_char_count: 5,
          after_char_count: 5,
        }],
      }],
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
    requested_ai_model: null,
    resolved_ai_model: null,
    can_cancel: status === "pending" || status === "running",
    can_retry: status === "failed",
    error_message: "",
  };
}

function loadRevisionPage({ api = {} } = {}) {
  mockMemoirApiModules({
    getMemoirBookRevisionProposal: async (_projectId, _proposalId, preview = "none") => (
      preview === "none"
        ? revisionProposalMetadata()
        : revisionProposalPreview(preview)
    ),
    enqueueMemoirBookRevisionFeedback: async () => revisionFeedbackJob("pending"),
    getMemoirBookRevisionFeedbackJob: async () => revisionFeedbackJob(),
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
    ...api,
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  return loadPageModule("../pages/book-revision/index.ts");
}

test("book revision page loads only the selected whole-book preview on demand", async () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/book-revision/index"));

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

test("book revision feedback refreshes the candidate before final confirmation", async () => {
  const calls = [];
  const proposal = revisionProposalMetadata();
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
        return revisionProposalMetadata({
          summary: "已经按意见更新。",
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
  assert.equal(context.data.proposal.summary, "已经按意见更新。");
  assert.ok(calls.some(([kind]) => kind === "reload"));
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
