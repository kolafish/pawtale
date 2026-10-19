const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const {
  captureStorageEntries,
  launchE2ESession,
  restoreStorageEntries,
  shutdownE2ESession,
} = require("../support/session");
const { sleep, waitForValue } = require("../support/wait");

let session;
let page;
let failed = false;
let originalRuntimeStorage = [];

const RUNTIME_STORAGE_KEYS = [
  "pawtale_backend_config",
  "pawtale_backend_cookie",
  "pawtale_accessibility_settings",
];

function storySection(id, title, memoryCount, index) {
  return {
    id,
    title,
    section_type: "life_fragment",
    sort_order: index - 1,
    status: "active",
    include_in_book: true,
    requires_user_assignment: false,
    version: 1,
    material_count: memoryCount,
    included_material_count: memoryCount,
    materials: [],
    indexText: String(index).padStart(2, "0"),
    typeText: "生活片段",
    metaText: `${memoryCount} 段回忆`,
    assignmentHint: "",
    mergeTargetId: "",
    mergeTargetTitle: "",
    hasDraft: true,
    draftContent: "小时候去外婆家，要沿着田边的小路走很久。",
    draftNeedsUpdate: false,
    expanded: false,
    visibleMaterials: [],
  };
}

function chapterData({ largeText = false } = {}) {
  const sections = [
    storySection("story-1", "小时候去外婆家的路", 11, 1),
    storySection("story-2", "夏天在河边乘凉", 6, 2),
  ];
  return {
    pageClass: `workspace-page ui-page-shell ui-accessible-page${largeText ? " large-text" : ""}`,
    loading: false,
    error: "",
    projectId: "visual-project",
    chapterId: "childhood",
    focusedSectionId: "",
    focusedSectionTitle: "",
    chapterHeroKicker: "已整理",
    chapterStoryCountText: "2 个故事",
    chapterStoryDetail: "",
    chapterHasReadableDraft: true,
    chapterEditingHint: "",
    narrativeSectionCount: 2,
    sectionStructureSummary: "2 个故事 · 17 段回忆",
    sectionViews: sections,
    requiredIssues: [],
    optionalIssues: [{
      id: "issue-1",
      project_id: "visual-project",
      scope: "chapter",
      chapter_id: "childhood",
      issue_type: "meaning",
      channel: "interview",
      question: "小时候去外婆家的路上，您还记得会经过哪些地方吗？",
      reason: "补充沿途记忆会让这个故事更完整。",
      priority: "medium",
      related_material_ids: [],
      input_signature: "visual-signature",
      status: "open",
      outcome_note: "",
      created_at: "2026-09-17T00:00:00Z",
      updated_at: "2026-09-17T00:00:00Z",
    }],
    editingToolsExpanded: false,
    operationKind: "",
    operationTargetId: "",
    editingMode: "",
    editingSectionId: "",
    editingSectionContent: "",
    canConfirmChapter: true,
    chapterPreparationJob: null,
    sectionRegenerationJob: null,
    audioPhase: "idle",
    audioTarget: "",
    retainedDraftSectionCount: 0,
    workspace: {
      project_id: "visual-project",
      chapter_id: "childhood",
      chapter_title: largeText ? "小时候和家人在故乡生活的漫长回忆" : "童年回忆",
      completion_summary: { state: "draft_ready" },
      draft_needs_rewrite: false,
      can_rewrite_draft: false,
      pending_assignment_material_count: 0,
      pending_completion_source_count: 0,
      pending_new_source_count: 0,
      sections,
      draft_id: "draft-1",
      draft_version_id: "version-1",
      draft_version: 1,
      draft_status: "draft",
      draft_content: "小时候的日子，是我一生中最温暖的起点。",
      draft_sections: sections.map((section) => ({
        section_id: section.id,
        title: section.title,
        section_type: section.section_type,
        section_version: 1,
        sort_order: section.sort_order,
        content: section.draftContent,
        used_material_ids: [],
      })),
    },
  };
}

before(async () => {
  session = await launchE2ESession({
    deferRuntimePreparation: true,
    initialPage: "/pages/login/index",
    requireAccount: false,
  });
  originalRuntimeStorage = await captureStorageEntries(
    session.miniProgram,
    RUNTIME_STORAGE_KEYS,
  );
  page = await session.miniProgram.reLaunch("/pages/chapter-workspace/index");
});

after(async () => {
  if (!session) return;
  let teardownError = null;
  try {
    await restoreStorageEntries(session.miniProgram, originalRuntimeStorage);
  } catch (error) {
    teardownError = error;
  }
  try {
    await shutdownE2ESession(session);
  } catch (error) {
    teardownError = teardownError
      ? new AggregateError([teardownError, error], "章节视觉测试存储恢复与开发者工具清理均失败")
      : error;
  }
  await session.artifacts.finish({
    failed: failed || Boolean(teardownError),
    target: "devtools-chapter-visual",
    teardownError,
  });
  if (teardownError) throw teardownError;
});

test("chapter overview renders the story-first visual hierarchy", async () => {
  try {
    await page.setData(chapterData());
    await sleep(500);

    const systemInfo = await session.miniProgram.systemInfo();
    const hero = await page.$(".chapter-hero");
    const readAction = await page.$(".chapter-read-primary");
    const storyRows = await page.$$(".story-overview-row");
    const suggestions = await waitForValue(
      () => page.$(".friendly-suggestions-card"),
      Boolean,
      { description: "补充建议卡片" },
    );
    const editingEntry = await page.$(".editing-entry");
    assert.ok(hero, "章节概览应以故事成长卡开场");
    assert.equal((await (await suggestions.$(".friendly-section-title")).text()).trim(), "还想听您讲讲");
    assert.equal(storyRows.length, 2, "章节概览应展示故事而不是后台小节摘要");
    assert.deepEqual(
      await Promise.all(storyRows.map(async (row) => (
        (await row.$(".story-overview-index")).text()
      ))),
      ["01", "02"],
      "故事目录使用稳定的两位编号，不重复装饰图标",
    );
    assert.ok(Number.parseFloat((await hero.size()).width) > systemInfo.windowWidth * 0.8);
    const readActionHeight = Number.parseFloat((await readAction.size()).height);
    const editingEntryHeight = Number.parseFloat((await editingEntry.size()).height);
    assert.ok(readActionHeight >= 40, `读章节按钮满足最小触控区域（实际 ${readActionHeight}px）`);
    assert.ok(editingEntryHeight >= 40, `整理入口满足最小触控区域（实际 ${editingEntryHeight}px）`);
    assert.equal(await page.$(".draft-card"), null, "高级整理默认收起");
    assert.equal(await page.$(".editing-entry-chevron.is-expanded"), null, "折叠态箭头应向下");

    await session.miniProgram.screenshot({
      path: path.join(session.artifacts.runPath, "chapter-story-overview.png"),
    });

    await editingEntry.trigger("tap");
    await sleep(350);
    assert.equal((await page.data()).editingToolsExpanded, true);
    assert.ok(await page.$(".draft-card"), "整理入口展开后仍保留完整编辑能力");
    assert.equal(await page.$(".story-overview-card"), null, "编辑态不应重复保留浏览故事列表");
    assert.equal((await page.$$(".section-item")).length, 2, "编辑态只展示同一组故事");
    assert.ok(await page.$(".editing-entry-chevron.is-expanded"), "展开态箭头应向上");
    const regroupAction = await page.$(".structure-regenerate-action");
    const regroupActionHeight = Number.parseFloat((await regroupAction.size()).height);
    assert.ok(regroupActionHeight >= 40, `重新分组工具项满足最小触控区域（实际 ${regroupActionHeight}px）`);
    await session.miniProgram.pageScrollTo(900);
    await sleep(300);
    await session.miniProgram.screenshot({
      path: path.join(session.artifacts.runPath, "chapter-editing-expanded.png"),
    });

    const confirmedData = chapterData();
    confirmedData.editingToolsExpanded = true;
    confirmedData.workspace.completion_summary = { state: "confirmed" };
    await page.setData(confirmedData);
    await sleep(250);
    const includedAction = await page.$(".chapter-finished-action");
    assert.ok(includedAction, "正文就绪后保留章节阅读入口");
    assert.equal(await page.$(".confirm-chapter-action"), null, "正文就绪后不再要求重复确认");
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});

test("chapter overview remains readable with large text and long titles", async () => {
  try {
    await session.miniProgram.pageScrollTo(0);
    await page.setData(chapterData({ largeText: true }));
    await sleep(400);
    const hero = await page.$(".chapter-hero");
    const pageSize = await page.size();
    assert.ok(Number.parseFloat((await hero.size()).width) <= Number.parseFloat(pageSize.width));
    await session.miniProgram.screenshot({
      path: path.join(session.artifacts.runPath, "chapter-large-text.png"),
    });
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
