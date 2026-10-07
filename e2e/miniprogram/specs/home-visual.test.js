const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const {
  captureStorageEntries,
  launchE2ESession,
  restoreStorageEntries,
  shutdownE2ESession,
} = require("../support/session");
const { sleep, waitForCurrentPage } = require("../support/wait");

const TREE_IMAGES = {
  1: "/assets/story-tree/tree-hero-stage-01-seed.jpg",
  2: "/assets/story-tree/tree-hero-stage-02-seedling.jpg",
  3: "/assets/story-tree/tree-hero-stage-03-sapling.jpg",
  4: "/assets/story-tree/tree-hero-stage-04-branching.jpg",
  5: "/assets/story-tree/tree-hero-stage-05-mature.jpg",
};

let session;
let homePage;
let failed = false;
let originalRuntimeStorage = [];

const RUNTIME_STORAGE_KEYS = [
  "pawtale_backend_config",
  "pawtale_backend_cookie",
  "pawtale_home_launch_state",
  "pawtale_memoir_session",
];

function story({ stage = 1, chapters = [], readable = false, bookChapters = 0, bookChars = 0 } = {}) {
  return {
    memoir: {
      title: "我的故事",
      progressDetail: readable ? "读读我的书" : "看看书稿",
      bookMetaText: bookChapters ? `已写成 ${bookChapters} 章 · ${bookChars} 字` : "故事会在这里慢慢写成书",
    },
    overview: {
      title: "我的故事树",
      summaryText: chapters.length
        ? `${chapters.length} 个主题 · ${chapters.reduce((sum, chapter) => sum + chapter.sections.length, 0)} 个故事`
        : "故事树还在等待第一段回忆",
      growthText: chapters.length
        ? "您的故事树已经很丰富了，还可以继续慢慢讲。"
        : "每一个故事，都从一颗种子开始。",
      growthStage: stage,
      growthImageSrc: TREE_IMAGES[stage],
      themeTags: chapters.slice(0, 3).map(({ id, title, tone }) => ({ id, title, tone })),
      hasChapters: chapters.length > 0,
      growthAnimating: false,
    },
    chapters,
  };
}

function chapter(id, title, storyCount, statusText, statusTone, tone) {
  return {
    id,
    hasSections: true,
    title,
    storyCountText: `${storyCount} 个故事`,
    statusText,
    statusTone,
    readable: statusTone === "rich",
    state: statusTone === "rich" ? "settled" : "attention",
    tone,
    highlighted: false,
    growthLabel: "",
    growthAnimating: false,
    sections: Array.from({ length: storyCount }, (_, index) => ({
      id: `${id}-story-${index + 1}`,
      title: `${title}的故事 ${index + 1}`,
      highlighted: false,
      growthLabel: "",
      growthAnimating: false,
    })),
  };
}

const richChapters = [
  chapter("childhood", "童年回忆", 3, "还可以聊聊", "continue", "clay"),
  chapter("meals", "日常饮食", 4, "已经很丰富", "rich", "sun"),
  chapter("exercise", "运动日常", 2, "已经很丰富", "rich", "leaf"),
  chapter("work", "工作与创业经历", 3, "还可以聊聊", "continue", "clay"),
  chapter("home", "辞职后的居家日常", 3, "还可以聊聊", "continue", "sun"),
];

function baseState() {
  return {
    loading: false,
    creatingVoice: false,
    voiceEntry: null,
    needsLogin: false,
    error: "",
    userId: "visual-user",
    displayName: "Kola",
    projectId: "visual-project",
    projectTitle: "Kola 的回忆录",
    projectDisplayTitle: "Kola 的回忆录",
    lastStoryHint: "",
    hasLastStoryHint: false,
    showNarrationChoiceCard: false,
    launchVariant: "none",
    hasProject: true,
    selectedMode: "free_narration",
    voiceAuthorizationAgreed: true,
    voiceAuthorizationRequested: false,
    greetingText: "你好，Kola",
    headerSubtitleText: "",
    voiceStartButtonClass: "",
    voiceStartButtonHoverClass: "voice-start-button-hover",
    primaryVoiceButtonTitle: "讲个新故事",
    showVoiceAuthorizationPanel: false,
    largeTextEnabled: false,
    homePageClass: "home-page",
    showContinueNarration: false,
    reviewNoticeText: "",
    reviewNoticeDetail: "",
    reviewNoticeAction: "",
    candidateNoticeText: "",
    candidateChapterId: "",
    story: story({ stage: 5, chapters: richChapters, readable: true, bookChapters: 3, bookChars: 1550 }),
    storyLoaded: true,
    hasStories: true,
    storyError: "",
    progress: null,
    bookReadable: true,
    growthEvents: [],
    growthNoticeTitle: "",
    growthNoticeText: "",
    growthNoticeContext: "",
    growthNoticeIcon: "",
    growthTarget: null,
    narrationSaved: false,
    dismissingGrowth: false,
    retryingDraft: false,
  };
}

function stateFixtures() {
  const base = baseState();
  return [
    {
      name: "logged-out",
      data: {
        ...base,
        needsLogin: true,
        userId: "",
        displayName: "作者",
        projectId: "",
        hasProject: false,
        hasStories: false,
        storyLoaded: false,
        greetingText: "你好，作者",
        headerSubtitleText: "登录后，把想说的故事慢慢留下来。",
        primaryVoiceButtonTitle: "登录后开始",
        story: story(),
      },
    },
    {
      name: "no-project",
      data: {
        ...base,
        projectId: "",
        projectTitle: "还没有回忆录",
        projectDisplayTitle: "还没有回忆录",
        hasProject: false,
        hasStories: false,
        headerSubtitleText: "每一个故事，都从一颗种子开始。",
        primaryVoiceButtonTitle: "种下我的故事",
        story: story(),
      },
    },
    {
      name: "empty-project",
      data: {
        ...base,
        hasStories: false,
        headerSubtitleText: "从一段回忆开始，让故事慢慢发芽。",
        primaryVoiceButtonTitle: "讲第一个故事",
        story: story(),
      },
    },
    {
      name: "populated",
      data: {
        ...base,
        hasLastStoryHint: true,
        showNarrationChoiceCard: true,
        lastStoryHint: "以前做饭，我还是喜欢做那种看上去很有食欲的菜……",
      },
      lowerScreenshot: true,
    },
    {
      name: "authorization-requested",
      data: {
        ...base,
        voiceAuthorizationAgreed: false,
        voiceAuthorizationRequested: true,
        showVoiceAuthorizationPanel: true,
        hasLastStoryHint: true,
        showNarrationChoiceCard: true,
        lastStoryHint: "说到小时候住过的那条老街。",
      },
      lowerScreenshot: true,
    },
    {
      name: "expanded-themes",
      data: {
        ...base,
        hasLastStoryHint: true,
        showNarrationChoiceCard: true,
        lastStoryHint: "其实很多年都没有去逛过书店了。",
      },
      expandThemes: true,
      lowerScreenshot: true,
    },
    {
      name: "growth-feedback",
      data: {
        ...base,
        hasLastStoryHint: true,
        showNarrationChoiceCard: true,
        lastStoryHint: "夏天的时候，我们常去河边乘凉。",
        growthNoticeTitle: "你的书又长了一点",
        growthNoticeText: "新增 1 个故事 · 书稿 +386 字",
        growthNoticeContext: "童年回忆等内容已更新",
        growthNoticeIcon: "/assets/story-tree/chapter-sprig.png",
        growthTarget: { chapterId: "childhood", sectionId: "childhood-story-1" },
        narrationSaved: true,
        story: story({
          stage: 5,
          chapters: richChapters.map((item, index) => index ? item : {
            ...item, highlighted: true, growthLabel: "新长出",
          }),
          readable: true,
          bookChapters: 3,
          bookChars: 1550,
        }),
      },
      lowerScreenshot: true,
    },
    {
      name: "cached-refresh-failure",
      data: {
        ...base,
        storyError: "进度暂未刷新，已保存的讲述和故事仍在，稍后会再试。",
        reviewNoticeText: "2 个故事等您确认",
        reviewNoticeDetail: "书里有 1 处等您确认",
        reviewNoticeAction: "issues",
      },
      lowerScreenshot: true,
    },
    {
      name: "draft-failed",
      data: {
        ...base,
        progress: {
          status: "failed",
          title: "这次整理没有完成",
          detail: "已经保存的回忆不会丢失，可以稍后继续整理。",
          can_retry: true,
        },
      },
      lowerScreenshot: true,
    },
    {
      name: "large-text-long-copy",
      data: {
        ...base,
        largeTextEnabled: true,
        homePageClass: "home-page large-text",
        displayName: "一位名字稍微长一些的讲述者",
        greetingText: "你好，一位名字稍微长一些的讲述者",
        hasLastStoryHint: true,
        showNarrationChoiceCard: true,
        lastStoryHint: "那一年全家从很远的地方搬到县城，第一次住进临街的旧房子。",
        story: story({
          stage: 4,
          chapters: richChapters.map((item, index) => ({
            ...item,
            title: index === 0 ? "小时候在故乡和家人一起生活的漫长回忆" : item.title,
          })),
          readable: true,
          bookChapters: 3,
          bookChars: 1550,
        }),
      },
      lowerScreenshot: true,
    },
  ];
}

async function renderFixture(fixture) {
  await session.miniProgram.pageScrollTo(0);
  await homePage.setData(fixture.data);
  await sleep(500);
  const tree = await homePage.$("#story-tree");
  assert.ok(tree, `${fixture.name} 应渲染故事树`);
  if (fixture.expandThemes) {
    await tree.callMethod("toggleAllChapters");
    await sleep(300);
  }
  const hero = await tree.$(".story-hero");
  const storySummaryToggle = fixture.data.story.overview.hasChapters
    ? await tree.$("#story-summary-toggle")
    : null;
  const primary = await homePage.$("#primary-voice-action");
  const resume = fixture.data.showNarrationChoiceCard ? await homePage.$("#resume-story-action") : null;
  assert.ok(hero, `${fixture.name} 应渲染故事树 Hero`);
  if (fixture.data.story.overview.hasChapters) {
    assert.ok(storySummaryToggle, `${fixture.name} 应渲染主题展开入口`);
  }
  assert.ok(primary, `${fixture.name} 应渲染主讲述按钮`);
  if (fixture.data.showNarrationChoiceCard) assert.ok(resume, `${fixture.name} 应渲染续讲入口`);
  if (fixture.expandThemes) {
    const chapterRows = await tree.$$(".story-row");
    assert.equal(chapterRows.length, fixture.data.story.chapters.length, `${fixture.name} 应在树卡内展示全部主题`);
  }

  const systemInfo = await session.miniProgram.systemInfo();
  const pageSize = await homePage.size();
  const heroSize = await hero.size();
  const primarySize = await primary.size();
  const pixels = value => Number.parseFloat(String(value));
  assert.ok(pixels(pageSize.width) <= pixels(systemInfo.windowWidth) + 1, `${fixture.name} 不应横向溢出`);
  assert.ok(pixels(heroSize.width) > pixels(systemInfo.windowWidth) * 0.8, `${fixture.name} Hero 应占据主视觉宽度`);
  assert.ok(
    pixels(primarySize.height) >= 44,
    `${fixture.name} 主按钮点击区域不小于 44px（实际 ${pixels(primarySize.height)}px）`,
  );
  if (storySummaryToggle) {
    const summaryToggleSize = await storySummaryToggle.size();
    assert.ok(
      pixels(summaryToggleSize.height) >= 44,
      `${fixture.name} 主题展开入口点击区域不小于 44px（实际 ${pixels(summaryToggleSize.height)}px）`,
    );
  }
  if (resume) {
    const resumeSize = await resume.size();
    assert.ok(
      pixels(resumeSize.height) >= 44,
      `${fixture.name} 续讲入口点击区域不小于 44px（实际 ${pixels(resumeSize.height)}px）`,
    );
  }

  await session.miniProgram.screenshot({
    path: path.join(session.artifacts.runPath, `home-${fixture.name}-top.png`),
  });
  if (fixture.lowerScreenshot) {
    await session.miniProgram.pageScrollTo(520);
    await sleep(300);
    await session.miniProgram.screenshot({
      path: path.join(session.artifacts.runPath, `home-${fixture.name}-lower.png`),
    });
  }
  if (fixture.expandThemes) {
    await tree.callMethod("toggleAllChapters");
    await sleep(250);
  }
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
  await session.miniProgram.callWxMethod("setStorageSync", "pawtale_backend_config", {
    httpBaseUrl: "http://127.0.0.1:9",
    wsBaseUrl: "ws://127.0.0.1:9",
  });
  await session.miniProgram.callWxMethod("removeStorageSync", "pawtale_backend_cookie");
  await session.miniProgram.callWxMethod("removeStorageSync", "pawtale_memoir_session");
  homePage = await session.miniProgram.reLaunch("/pages/home/index");
  if (!homePage) homePage = await waitForCurrentPage(session.miniProgram, "pages/home/index");
  await homePage.callMethod("onHide");
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
      ? new AggregateError([teardownError, error], "视觉测试存储恢复与开发者工具清理均失败")
      : error;
  }
  await session.artifacts.finish({
    failed: failed || Boolean(teardownError),
    target: "devtools-home-visual-matrix",
    teardownError,
  });
  if (teardownError) throw teardownError;
});

test("home renders the complete account and story-state visual matrix", async () => {
  try {
    for (const fixture of stateFixtures()) await renderFixture(fixture);
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
