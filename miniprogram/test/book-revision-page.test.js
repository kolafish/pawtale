require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");

function revisionProposal(overrides = {}) {
  const beforeSection = {
    section_id: "section-1",
    index: 1,
    title: "旧校舍",
    char_count: 4,
    source_count: 1,
    section_type: "event",
    content: "修改前正文",
    source_ref_ids: [],
  };
  const afterSection = { ...beforeSection, content: "修改后正文" };
  const chapter = (section) => ({
    chapter_id: "chapter-1",
    index: 1,
    title: "上学时光",
    subtitle: "",
    intro: "",
    time_hint: "",
    place_hint: "",
    pull_quotes: [],
    people_notes: [],
    uncertain_facts: [],
    content: section.content,
    sections: [section],
    source_refs: [],
    illustrations: [],
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

function loadRevisionPage({ api = {} } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookRevisionProposal: async () => revisionProposal(),
    applyMemoirBookRevisionProposal: async () => revisionProposal({
      status: "applied",
      can_apply: false,
      can_dismiss: false,
      result_output_version_id: "version-2",
    }),
    dismissMemoirBookRevisionProposal: async () => revisionProposal({
      status: "dismissed",
      can_apply: false,
      can_dismiss: false,
    }),
    ...api,
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  return loadPageModule("../pages/book-revision/index.ts");
}

test("book revision page is registered and switches between before and after text", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/book-revision/index"));

  const { revisionViewPatch } = loadRevisionPage().mod;
  const proposal = revisionProposal();
  const after = revisionViewPatch(proposal);
  const before = revisionViewPatch(proposal, ["section-1"], "before");

  assert.equal(after.changedChapters[0].sections[0].display_content, "修改后正文");
  assert.equal(before.changedChapters[0].sections[0].display_content, "修改前正文");
  assert.equal(before.wholeChapters[0].sections[0].content, "修改前正文");
  assert.equal(after.proposal.comparison.before_chapter_order[0].title, "上学时光");
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
        return revisionProposal({
          status: "applied",
          can_apply: false,
          can_dismiss: false,
          result_output_version_id: "version-2",
        });
      },
      dismissMemoirBookRevisionProposal: async (projectId, proposalId) => {
        actions.push(["dismiss", projectId, proposalId]);
        return revisionProposal({ status: "dismissed" });
      },
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    proposalId: "proposal-1",
    proposal: revisionProposal(),
  });

  await page.applyRevision.call(context);
  context.data.proposal = revisionProposal();
  context.data.busy = false;
  await page.dismissRevision.call(context);

  assert.deepEqual(actions, [
    ["apply", "project-1", "proposal-1"],
    ["dismiss", "project-1", "proposal-1"],
    "back",
  ]);
});
