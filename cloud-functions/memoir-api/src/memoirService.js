const { ApiError } = require("./http");
const { COLLECTIONS } = require("./collections");
const {
  DEFAULT_COVERAGE_TOPICS,
  DEFAULT_MEMOIR_QUESTIONS,
  DEFAULT_SESSION_TITLE,
} = require("./memoirTemplate");

const MemoirQuestionStatus = Object.freeze({
  PENDING: "pending",
  ANSWERED: "answered",
  SKIPPED: "skipped",
});

const MemoirSessionStatus = Object.freeze({
  ACTIVE: "active",
  COMPLETED: "completed",
});

const MemoirAnswerStatus = Object.freeze({
  UPLOADED: "uploaded",
  ANSWERED: "answered",
  DRAFTED: "drafted",
  CONFIRMED: "confirmed",
});

const MemoirDraftStatus = Object.freeze({
  DRAFT: "draft",
  ACCEPTED: "accepted",
});

const MemoirMemoryStatus = Object.freeze({
  DRAFT: "draft",
  CONFIRMED: "confirmed",
  REJECTED: "rejected",
});

const MemoirCreatedBy = Object.freeze({
  AI: "ai",
  SYSTEM: "system",
  USER: "user",
});

const MemoirMemoryType = Object.freeze({
  PERSON: "person",
  EVENT: "event",
  PLACE: "place",
  THEME: "theme",
  OPEN_QUESTION: "open_question",
});

const MemoirOpenQuestionStatus = Object.freeze({
  OPEN: "open",
  ASKED: "asked",
  ANSWERED: "answered",
  DISMISSED: "dismissed",
});

const MemoirCoverageStatus = Object.freeze({
  RED: "red",
  YELLOW: "yellow",
  GREEN: "green",
});

const MemoirQuestionCandidateStatus = Object.freeze({
  CANDIDATE: "candidate",
  SELECTED: "selected",
  ASKED: "asked",
  DISMISSED: "dismissed",
});

const MemoirOutputType = Object.freeze({
  STORY_FRAGMENT: "story_fragment",
});

const MemoirOutputStatus = Object.freeze({
  DRAFT: "draft",
});

const STORY_PREVIEW_LIMIT = 80;
const MAX_AUDIO_BYTES = 20 * 1024 * 1024;
const CLOUD_AUDIO_ROOT = "memoir/audio";
const AUDIO_MIME_BY_EXTENSION = Object.freeze({
  mp3: "audio/mpeg",
  wav: "audio/wav",
  ogg: "audio/ogg",
  pcm: "audio/pcm",
});
const AUDIO_EXTENSION_BY_MIME = Object.freeze({
  "audio/mpeg": "mp3",
  "audio/mp3": "mp3",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
  "audio/wave": "wav",
  "audio/ogg": "ogg",
  "audio/pcm": "pcm",
  "audio/x-pcm": "pcm",
});
const GENERIC_AUDIO_MIME_TYPES = new Set(["", "application/octet-stream"]);

function nowIso() {
  return new Date().toISOString();
}

function weekStartIso(now = new Date()) {
  const date = new Date(now);
  const daysSinceMonday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - daysSinceMonday);
  date.setUTCHours(0, 0, 0, 0);
  return date.toISOString();
}

function requireOpenId(context) {
  if (!context.openid) {
    throw new ApiError(401, "缺少微信登录身份");
  }
  return context.openid;
}

function createProjectResponse(project) {
  return {
    id: project.id,
    type: "memoir",
    title: project.title,
    description: project.description || "",
    total_word_count: project.total_word_count || 0,
    groups: [],
    references: [],
    created_at: project.created_at,
    updated_at: project.updated_at,
  };
}

function cleanAnswerText(text) {
  const value = String(text || "").trim();
  if (!value) {
    throw new ApiError(400, "回答不能为空");
  }
  return value;
}

function cleanDraftText(text) {
  const value = String(text || "").trim();
  if (!value) {
    throw new ApiError(400, "整理稿不能为空");
  }
  return value;
}

function answerText(answer) {
  const text = String(answer.transcript_edited || answer.transcript_raw || "").trim();
  if (!text) {
    throw new ApiError(400, "回答内容为空，无法整理");
  }
  return text;
}

function previewText(text) {
  const value = String(text || "").trim().replace(/\s+/g, " ");
  if (value.length <= STORY_PREVIEW_LIMIT) {
    return value;
  }
  return `${value.slice(0, STORY_PREVIEW_LIMIT)}...`;
}

function cleanPositiveNumber(value, fallback = null) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue) || numberValue < 0) {
    return fallback;
  }
  return Math.round(numberValue);
}

function cleanText(value) {
  return String(value || "").trim();
}

function optionalText(value) {
  const text = cleanText(value);
  return text || null;
}

function arrayField(value) {
  return Array.isArray(value)
    ? value.map((entry) => cleanText(entry)).filter(Boolean)
    : [];
}

function items(payload, key) {
  const value = payload && payload[key];
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object" && !Array.isArray(item)) : [];
}

function boundedFloat(value, fallback = null) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) {
    return fallback;
  }
  return Math.max(0, Math.min(1, numberValue));
}

function positiveInt(value, fallback = 1) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) {
    return fallback;
  }
  return Math.max(1, Math.round(numberValue));
}

function normalizeLabel(value) {
  return cleanText(value).toLowerCase();
}

function parseJsonArray(value) {
  if (Array.isArray(value)) {
    return value;
  }
  if (!value) {
    return [];
  }
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function parseJsonObject(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value;
  }
  if (!value) {
    return {};
  }
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function questionType(value, fallback = "clarification") {
  const text = cleanText(value).toLowerCase();
  const aliases = {
    scene_detail: "scene",
    person_detail: "person",
    emotion_detail: "emotion",
  };
  const normalized = aliases[text] || text;
  return [
    "clarification",
    "depth",
    "scene",
    "person",
    "emotion",
    "connection",
    "closure",
  ].includes(normalized) ? normalized : fallback;
}

function sensitivity(value) {
  const text = cleanText(value).toLowerCase();
  return ["low", "medium", "high"].includes(text) ? text : "low";
}

function cleanRequiredText(value, message) {
  const text = String(value || "").trim();
  if (!text) {
    throw new ApiError(400, message);
  }
  return text;
}

function safePathSegment(value) {
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function audioExtension(cloudPath) {
  const match = String(cloudPath || "").toLowerCase().match(/\.([a-z0-9]+)$/);
  return match ? match[1] : "";
}

function cleanCloudFileId(value, cloudPath) {
  const fileId = cleanRequiredText(value, "缺少云存储文件 ID");
  if (!fileId.startsWith("cloud://")) {
    throw new ApiError(400, "云存储文件 ID 无效");
  }
  if (!fileId.endsWith(`/${cloudPath}`)) {
    throw new ApiError(403, "云存储文件 ID 与音频路径不匹配");
  }
  return fileId;
}

function cleanCloudAudioPath(openid, value) {
  const cloudPath = cleanRequiredText(value, "缺少云存储路径");
  if (cloudPath.includes("..") || cloudPath.includes("//")) {
    throw new ApiError(400, "云存储路径无效");
  }

  const ownerPrefix = `${CLOUD_AUDIO_ROOT}/${safePathSegment(openid)}/`;
  if (!cloudPath.startsWith(ownerPrefix)) {
    throw new ApiError(403, "音频路径不属于当前用户");
  }

  const extension = audioExtension(cloudPath);
  if (!AUDIO_MIME_BY_EXTENSION[extension]) {
    throw new ApiError(400, "仅支持 mp3、wav、ogg、pcm 音频文件");
  }
  return cloudPath;
}

function cleanAudioMimeType(value, extension) {
  const mimeType = String(value || "").split(";")[0].trim().toLowerCase();
  const expectedMimeType = AUDIO_MIME_BY_EXTENSION[extension];
  if (GENERIC_AUDIO_MIME_TYPES.has(mimeType)) {
    return expectedMimeType;
  }
  if (AUDIO_EXTENSION_BY_MIME[mimeType] !== extension) {
    throw new ApiError(400, "音频类型与文件扩展名不匹配");
  }
  return expectedMimeType;
}

function cleanAudioSizeBytes(value) {
  if (value === undefined || value === null || value === "") {
    throw new ApiError(400, "缺少音频大小");
  }
  const sizeBytes = Number(value);
  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) {
    throw new ApiError(400, "音频文件不能为空");
  }
  if (sizeBytes > MAX_AUDIO_BYTES) {
    throw new ApiError(413, "音频文件不能超过 20MB");
  }
  return Math.round(sizeBytes);
}

function serializeJsonField(value) {
  if (value === undefined || value === null) {
    return "";
  }
  return JSON.stringify(value);
}

async function getOrCreateProject(store, openid) {
  const existing = await store.findOne(COLLECTIONS.projects, {
    openid,
    type: "memoir",
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });

  if (existing) {
    return existing;
  }

  const now = nowIso();
  return store.insert(COLLECTIONS.projects, {
    openid,
    type: "memoir",
    title: "我的回忆录",
    description: "",
    total_word_count: 0,
    created_at: now,
    updated_at: now,
  });
}

async function getOrCreateCurrentSession(store, openid) {
  const project = await getOrCreateProject(store, openid);
  const existing = await store.findOne(COLLECTIONS.sessions, {
    openid,
    project_id: project.id,
    status: MemoirSessionStatus.ACTIVE,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });

  const session = existing || await createSession(store, openid, project.id);
  return getSessionState(store, openid, session.id);
}

async function createSession(store, openid, projectId) {
  const now = nowIso();
  const session = await store.insert(COLLECTIONS.sessions, {
    openid,
    project_id: projectId,
    title: DEFAULT_SESSION_TITLE,
    status: MemoirSessionStatus.ACTIVE,
    current_question_id: null,
    created_at: now,
    updated_at: now,
  });

  const questions = [];
  for (const [index, prompt] of DEFAULT_MEMOIR_QUESTIONS.entries()) {
    const question = await store.insert(COLLECTIONS.questions, {
      openid,
      session_id: session.id,
      prompt,
      source: "template",
      sort_order: index,
      status: MemoirQuestionStatus.PENDING,
      created_at: nowIso(),
    });
    questions.push(question);
  }

  if (questions[0]) {
    return store.updateById(COLLECTIONS.sessions, session.id, {
      current_question_id: questions[0].id,
      updated_at: nowIso(),
    });
  }

  return session;
}

async function loadSession(store, openid, sessionId) {
  const session = await store.findOne(COLLECTIONS.sessions, {
    openid,
    _id: sessionId,
  });
  if (!session) {
    throw new ApiError(404, "访谈会话不存在");
  }
  return session;
}

async function loadQuestion(store, openid, questionId) {
  const question = await store.findOne(COLLECTIONS.questions, {
    openid,
    _id: questionId,
  });
  if (!question) {
    throw new ApiError(404, "问题不存在");
  }
  return question;
}

async function loadAnswer(store, openid, answerId) {
  const answer = await store.findOne(COLLECTIONS.answers, {
    openid,
    _id: answerId,
  });
  if (!answer) {
    throw new ApiError(404, "回答不存在");
  }
  return answer;
}

async function loadDraft(store, openid, draftId) {
  const draft = await store.findOne(COLLECTIONS.drafts, {
    openid,
    _id: draftId,
  });
  if (!draft) {
    throw new ApiError(404, "整理稿不存在");
  }
  return draft;
}

async function getOrderedQuestions(store, openid, sessionId) {
  return store.findMany(COLLECTIONS.questions, {
    openid,
    session_id: sessionId,
  }, {
    orderBy: [{ field: "sort_order", direction: "asc" }],
  });
}

async function getLatestDraft(store, openid, answers) {
  const drafts = [];
  for (const answer of answers) {
    const draft = await store.findOne(COLLECTIONS.drafts, {
      openid,
      answer_id: answer.id,
    }, {
      orderBy: [{ field: "updated_at", direction: "desc" }],
    });
    if (draft) {
      drafts.push(draft);
    }
  }

  return drafts.sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] || null;
}

function memoryCollection(memoryType) {
  const byType = {
    [MemoirMemoryType.PERSON]: COLLECTIONS.personMemories,
    [MemoirMemoryType.EVENT]: COLLECTIONS.eventMemories,
    [MemoirMemoryType.PLACE]: COLLECTIONS.placeMemories,
    [MemoirMemoryType.THEME]: COLLECTIONS.themeMemories,
    [MemoirMemoryType.OPEN_QUESTION]: COLLECTIONS.openQuestions,
  };
  if (!byType[memoryType]) {
    throw new ApiError(400, "记忆类型不支持");
  }
  return byType[memoryType];
}

async function loadMemory(store, openid, projectId, memoryType, memoryId) {
  const memory = await store.findOne(memoryCollection(memoryType), {
    openid,
    project_id: projectId,
    _id: memoryId,
  });
  if (!memory) {
    throw new ApiError(404, "记忆不存在");
  }
  return memory;
}

async function listMemoryRows(store, openid, projectId, memoryType) {
  return store.findMany(memoryCollection(memoryType), {
    openid,
    project_id: projectId,
  }, {
    orderBy: [{ field: "created_at", direction: "asc" }],
  });
}

async function listOpenQuestions(store, openid) {
  const project = await getOrCreateProject(store, openid);
  return store.findMany(COLLECTIONS.openQuestions, {
    openid,
    project_id: project.id,
  }, {
    orderBy: [{ field: "priority", direction: "desc" }, { field: "created_at", direction: "asc" }],
  });
}

async function updateMemory(store, openid, memoryType, memoryId, data = {}) {
  if (memoryType === MemoirMemoryType.OPEN_QUESTION) {
    throw new ApiError(400, "待确认问题请使用 open questions 接口");
  }
  const project = await getOrCreateProject(store, openid);
  await loadMemory(store, openid, project.id, memoryType, memoryId);
  const patch = {
    updated_at: nowIso(),
  };
  if (data.status !== undefined) {
    if (!Object.values(MemoirMemoryStatus).includes(data.status)) {
      throw new ApiError(400, "记忆状态不支持");
    }
    patch.status = data.status;
  }
  const textFieldsByType = {
    [MemoirMemoryType.PERSON]: ["canonical_name", "relationship", "description"],
    [MemoirMemoryType.EVENT]: ["title", "life_stage", "time_text", "place_text", "cause", "process", "result", "emotion"],
    [MemoirMemoryType.PLACE]: ["name", "elder_alias", "modern_name", "place_type", "description"],
    [MemoirMemoryType.THEME]: ["name", "description"],
  };
  for (const field of textFieldsByType[memoryType] || []) {
    if (data[field] !== undefined) {
      patch[field] = typeof data[field] === "string" ? data[field].trim() : data[field];
    }
  }
  for (const field of ["confidence", "importance", "strength"]) {
    if (data[field] !== undefined) {
      patch[field] = boundedFloat(data[field]);
    }
  }
  if (data.mention_count !== undefined) {
    patch.mention_count = positiveInt(data.mention_count);
  }
  return store.updateById(memoryCollection(memoryType), memoryId, patch);
}

async function updateOpenQuestion(store, openid, questionId, data = {}) {
  const project = await getOrCreateProject(store, openid);
  const existing = await loadMemory(store, openid, project.id, MemoirMemoryType.OPEN_QUESTION, questionId);
  const patch = {
    updated_at: nowIso(),
  };
  if (data.status !== undefined) {
    if (!Object.values(MemoirOpenQuestionStatus).includes(data.status)) {
      throw new ApiError(400, "待确认问题状态不支持");
    }
    patch.status = data.status;
  }
  if (data.question !== undefined) {
    patch.question = cleanRequiredText(data.question, "问题不能为空");
  }
  if (data.reason !== undefined) {
    patch.reason = cleanText(data.reason);
  }
  if (data.priority !== undefined) {
    patch.priority = boundedFloat(data.priority);
  }
  if (data.sensitivity !== undefined) {
    patch.sensitivity = sensitivity(data.sensitivity);
  }
  return store.updateById(COLLECTIONS.openQuestions, existing.id, patch);
}

async function getProjectRows(store, openid) {
  const project = await getOrCreateProject(store, openid);
  const sessions = await store.findMany(COLLECTIONS.sessions, {
    openid,
    project_id: project.id,
  });
  const questions = [];
  for (const session of sessions) {
    questions.push(...await getOrderedQuestions(store, openid, session.id));
  }

  const questionIds = new Set(questions.map((question) => question.id));
  const answers = (await store.findMany(COLLECTIONS.answers, { openid }))
    .filter((answer) => questionIds.has(answer.question_id));
  const answerIds = new Set(answers.map((answer) => answer.id));
  const drafts = (await store.findMany(COLLECTIONS.drafts, { openid }))
    .filter((draft) => answerIds.has(draft.answer_id));

  return { project, sessions, questions, answers, drafts };
}

function storyCardsFromRows(rows) {
  const sessionById = new Map(rows.sessions.map((session) => [session.id, session]));
  const questionById = new Map(rows.questions.map((question) => [question.id, question]));
  const draftByAnswerId = new Map(rows.drafts.map((draft) => [draft.answer_id, draft]));

  return rows.answers
    .map((answer) => {
      const question = questionById.get(answer.question_id);
      const session = question ? sessionById.get(question.session_id) : null;
      if (!question || !session) {
        return null;
      }
      const draft = draftByAnswerId.get(answer.id) || null;
      return {
        id: answer.id,
        answer_id: answer.id,
        question_id: question.id,
        session_id: session.id,
        session_title: session.title,
        question_prompt: question.prompt,
        answer_preview: previewText(answer.transcript_edited || answer.transcript_raw),
        answer_status: answer.status,
        draft_id: draft ? draft.id : null,
        draft_status: draft ? draft.status : null,
        draft_preview: draft ? previewText(draft.content_edited || draft.content_raw) : null,
        duration_seconds: answer.duration_seconds ?? null,
        created_at: answer.created_at,
        updated_at: answer.updated_at,
      };
    })
    .filter(Boolean)
    .sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
}

function storyDetailFromRows(rows, answerId) {
  const cards = storyCardsFromRows(rows);
  const story = cards.find((card) => card.answer_id === answerId);
  if (!story) {
    throw new ApiError(404, "故事不存在");
  }

  const question = rows.questions.find((item) => item.id === story.question_id);
  const answer = rows.answers.find((item) => item.id === story.answer_id);
  const draft = rows.drafts.find((item) => item.answer_id === story.answer_id) || null;
  if (!question || !answer) {
    throw new ApiError(404, "故事不存在");
  }

  return {
    story,
    question,
    answer,
    draft,
  };
}

async function listStoryCards(store, openid) {
  const stories = storyCardsFromRows(await getProjectRows(store, openid));
  return {
    stories,
    total: stories.length,
  };
}

async function getStoryDetail(store, openid, answerId) {
  return storyDetailFromRows(await getProjectRows(store, openid), answerId);
}

async function getSummary(store, openid) {
  await getOrCreateCurrentSession(store, openid);
  const rows = await getProjectRows(store, openid);
  const stories = storyCardsFromRows(rows);
  const completedStatuses = new Set([
    MemoirQuestionStatus.ANSWERED,
    MemoirQuestionStatus.SKIPPED,
  ]);
  const completedQuestionCount = rows.questions
    .filter((question) => completedStatuses.has(question.status))
    .length;
  const totalQuestionCount = rows.questions.length;
  const weekStart = weekStartIso();

  return {
    total_story_count: stories.length,
    weekly_story_count: stories.filter((story) => String(story.created_at) >= weekStart).length,
    pending_review_count: stories.filter((story) => story.draft_status === MemoirDraftStatus.DRAFT).length,
    confirmed_story_count: stories.filter((story) => story.draft_status === MemoirDraftStatus.ACCEPTED).length,
    total_question_count: totalQuestionCount,
    completed_question_count: completedQuestionCount,
    progress_percent: totalQuestionCount
      ? Math.round((completedQuestionCount * 100) / totalQuestionCount)
      : 0,
  };
}

async function collectSessionTurns(store, openid, sessionId) {
  const questions = await getOrderedQuestions(store, openid, sessionId);
  const turns = [];
  for (const question of questions) {
    const answer = await store.findOne(COLLECTIONS.answers, {
      openid,
      question_id: question.id,
    });
    const text = cleanText(answer?.transcript_edited || answer?.transcript_raw);
    if (!answer || !text) {
      continue;
    }
    turns.push({
      session_id: sessionId,
      question_id: question.id,
      answer_id: answer.id,
      question: question.prompt,
      answer_text: text,
    });
  }
  return turns;
}

function findTurn(turns, item) {
  const answerId = cleanText(item.answer_id);
  const questionId = cleanText(item.question_id);
  const quote = cleanText(item.evidence_quote || item.quote);
  return turns.find((turn) => answerId && turn.answer_id === answerId)
    || turns.find((turn) => questionId && turn.question_id === questionId)
    || turns.find((turn) => quote && turn.answer_text.includes(quote))
    || turns[0];
}

async function findMemoryByLabel(store, openid, projectId, collection, field, label) {
  const rows = await store.findMany(collection, {
    openid,
    project_id: projectId,
  });
  const target = normalizeLabel(label);
  return rows.find((row) => normalizeLabel(row[field]) === target) || null;
}

async function upsertMemoryByLabel(store, openid, projectId, collection, field, label, data) {
  const now = nowIso();
  const existing = await findMemoryByLabel(store, openid, projectId, collection, field, label);
  if (existing) {
    return existing;
  }
  return store.insert(collection, {
    openid,
    project_id: projectId,
    status: MemoirMemoryStatus.DRAFT,
    created_by: MemoirCreatedBy.AI,
    created_at: now,
    updated_at: now,
    ...data,
  });
}

async function addEvidence(store, openid, projectId, turns, memoryType, memoryId, item) {
  const turn = findTurn(turns, item);
  if (!turn) {
    return null;
  }
  const quote = cleanText(item.evidence_quote || item.quote) || turn.answer_text.slice(0, 300);
  const existing = await store.findOne(COLLECTIONS.memoryEvidence, {
    openid,
    project_id: projectId,
    memory_type: memoryType,
    memory_id: memoryId,
    session_id: turn.session_id,
    question_id: turn.question_id,
    answer_id: turn.answer_id,
    quote,
  });
  if (existing) {
    return existing;
  }
  return store.insert(COLLECTIONS.memoryEvidence, {
    openid,
    project_id: projectId,
    memory_type: memoryType,
    memory_id: memoryId,
    session_id: turn.session_id,
    question_id: turn.question_id,
    answer_id: turn.answer_id,
    quote,
    confidence: boundedFloat(item.confidence),
    created_at: nowIso(),
  });
}

function memoryLookupKey(memoryType, label) {
  return `${memoryType}:${normalizeLabel(label)}`;
}

function resolveTarget(item, createdMemoryIds) {
  const type = cleanText(item.target_memory_type || item.target_type);
  if (![MemoirMemoryType.PERSON, MemoirMemoryType.EVENT, MemoirMemoryType.PLACE, MemoirMemoryType.THEME].includes(type)) {
    return [null, null];
  }
  const label = cleanText(
    item.target_title
      || item.target_name
      || item.target_memory_title
      || item.event_title
      || item.person_name
      || item.place_name
      || item.theme_name,
  );
  const memoryId = label ? createdMemoryIds.get(memoryLookupKey(type, label)) : null;
  return memoryId ? [type, memoryId] : [null, null];
}

function slimPeople(rows) {
  return rows.map((item) => ({
    id: item.id,
    canonical_name: item.canonical_name,
    relationship: item.relationship,
    confidence: item.confidence,
  }));
}

function slimEvents(rows) {
  return rows.map((item) => ({
    id: item.id,
    title: item.title,
    life_stage: item.life_stage,
    time_text: item.time_text,
    place_text: item.place_text,
    confidence: item.confidence,
  }));
}

function slimPlaces(rows) {
  return rows.map((item) => ({
    id: item.id,
    name: item.name,
    elder_alias: item.elder_alias,
    modern_name: item.modern_name,
    confidence: item.confidence,
  }));
}

function slimThemes(rows) {
  return rows.map((item) => ({
    id: item.id,
    name: item.name,
    strength: item.strength,
    mention_count: item.mention_count,
    confidence: item.confidence,
  }));
}

function slimOpenQuestions(rows) {
  return rows.map((item) => ({
    id: item.id,
    question: item.question,
    question_type: item.question_type,
    reason: item.reason,
    sensitivity: item.sensitivity,
    priority: item.priority,
  }));
}

async function upsertSessionSummary(store, openid, projectId, sessionId, data) {
  const now = nowIso();
  const existing = await store.findOne(COLLECTIONS.sessionSummaries, {
    openid,
    session_id: sessionId,
  });
  const patch = {
    project_id: projectId,
    summary: data.summary,
    new_people_json: data.new_people_json,
    new_events_json: data.new_events_json,
    new_places_json: data.new_places_json,
    new_themes_json: data.new_themes_json,
    open_questions_json: data.open_questions_json,
    next_question_suggestions_json: data.next_question_suggestions_json,
    updated_at: now,
  };
  if (existing) {
    return store.updateById(COLLECTIONS.sessionSummaries, existing.id, patch);
  }
  return store.insert(COLLECTIONS.sessionSummaries, {
    openid,
    session_id: sessionId,
    created_at: now,
    ...patch,
  });
}

async function getSessionSummary(store, openid, sessionId) {
  const summary = await store.findOne(COLLECTIONS.sessionSummaries, {
    openid,
    session_id: sessionId,
  });
  return summary || null;
}

async function upsertSessionOutputVersion(store, openid, projectId, sessionId, data) {
  const now = nowIso();
  const existing = await store.findOne(COLLECTIONS.outputs, {
    openid,
    project_id: projectId,
    source_session_id: sessionId,
    output_type: data.output_type,
  });
  const output = existing
    ? await store.updateById(COLLECTIONS.outputs, existing.id, {
      title: data.title,
      status: MemoirOutputStatus.DRAFT,
      updated_at: now,
    })
    : await store.insert(COLLECTIONS.outputs, {
      openid,
      project_id: projectId,
      output_type: data.output_type,
      title: data.title,
      source_session_id: sessionId,
      status: MemoirOutputStatus.DRAFT,
      created_at: now,
      updated_at: now,
    });
  const versions = await store.findMany(COLLECTIONS.outputVersions, {
    openid,
    output_id: output.id,
  });
  const version = versions.length
    ? Math.max(...versions.map((item) => Number(item.version) || 0)) + 1
    : 1;
  await store.insert(COLLECTIONS.outputVersions, {
    openid,
    output_id: output.id,
    version,
    content: data.content,
    model: data.model || null,
    prompt_snapshot: data.prompt_snapshot || "",
    feedback: null,
    created_at: now,
  });
  return output;
}

async function processSessionMemory(store, openid, sessionId, dependencies) {
  const session = await loadSession(store, openid, sessionId);
  const turns = await collectSessionTurns(store, openid, session.id);
  if (!turns.length) {
    throw new ApiError(400, "本次访谈没有可处理的回答");
  }
  if (!dependencies.memoryExtraction?.extract) {
    throw new ApiError(500, "结构化记忆提取服务未配置");
  }
  const extraction = await dependencies.memoryExtraction.extract({ session, turns });
  const payload = extraction.payload || {};
  const projectId = session.project_id;
  const createdMemoryIds = new Map();

  const people = [];
  for (const item of items(payload, "people")) {
    const canonicalName = cleanText(item.canonical_name || item.name);
    if (!canonicalName) {
      continue;
    }
    const person = await upsertMemoryByLabel(store, openid, projectId, COLLECTIONS.personMemories, "canonical_name", canonicalName, {
      canonical_name: canonicalName,
      aliases_json: arrayField(item.aliases || item.aliases_json),
      relationship: optionalText(item.relationship),
      first_seen_session_id: session.id,
      description: cleanText(item.description),
      confidence: boundedFloat(item.confidence),
    });
    people.push(person);
    createdMemoryIds.set(memoryLookupKey(MemoirMemoryType.PERSON, person.canonical_name), person.id);
    await addEvidence(store, openid, projectId, turns, MemoirMemoryType.PERSON, person.id, item);
  }

  const events = [];
  for (const item of items(payload, "events")) {
    const title = cleanText(item.title || item.event_title);
    if (!title) {
      continue;
    }
    const event = await upsertMemoryByLabel(store, openid, projectId, COLLECTIONS.eventMemories, "title", title, {
      title,
      life_stage: optionalText(item.life_stage),
      time_text: optionalText(item.time_text || item.time),
      place_text: optionalText(item.place_text || item.place),
      cause: cleanText(item.cause),
      process: cleanText(item.process || item.description),
      result: cleanText(item.result),
      emotion: optionalText(item.emotion),
      importance: boundedFloat(item.importance),
      confidence: boundedFloat(item.confidence),
    });
    events.push(event);
    createdMemoryIds.set(memoryLookupKey(MemoirMemoryType.EVENT, event.title), event.id);
    await addEvidence(store, openid, projectId, turns, MemoirMemoryType.EVENT, event.id, item);
  }

  const places = [];
  for (const item of items(payload, "places")) {
    const name = cleanText(item.name || item.place_name);
    if (!name) {
      continue;
    }
    const place = await upsertMemoryByLabel(store, openid, projectId, COLLECTIONS.placeMemories, "name", name, {
      name,
      elder_alias: optionalText(item.elder_alias || item.alias),
      modern_name: optionalText(item.modern_name),
      place_type: optionalText(item.place_type || item.type),
      description: cleanText(item.description),
      confidence: boundedFloat(item.confidence),
    });
    places.push(place);
    createdMemoryIds.set(memoryLookupKey(MemoirMemoryType.PLACE, place.name), place.id);
    await addEvidence(store, openid, projectId, turns, MemoirMemoryType.PLACE, place.id, item);
  }

  const themes = [];
  for (const item of items(payload, "themes")) {
    const name = cleanText(item.name || item.theme);
    if (!name) {
      continue;
    }
    const theme = await upsertMemoryByLabel(store, openid, projectId, COLLECTIONS.themeMemories, "name", name, {
      name,
      description: cleanText(item.description),
      strength: boundedFloat(item.strength),
      mention_count: positiveInt(item.mention_count),
      confidence: boundedFloat(item.confidence),
    });
    themes.push(theme);
    createdMemoryIds.set(memoryLookupKey(MemoirMemoryType.THEME, theme.name), theme.id);
    await addEvidence(store, openid, projectId, turns, MemoirMemoryType.THEME, theme.id, item);
  }

  const openQuestions = [];
  for (const item of items(payload, "open_questions")) {
    const question = cleanText(item.question);
    if (!question) {
      continue;
    }
    const [targetMemoryType, targetMemoryId] = resolveTarget(item, createdMemoryIds);
    const openQuestion = await upsertMemoryByLabel(store, openid, projectId, COLLECTIONS.openQuestions, "question", question, {
      session_id: session.id,
      target_memory_type: targetMemoryType,
      target_memory_id: targetMemoryId,
      question,
      question_type: questionType(item.question_type || item.type),
      reason: cleanText(item.reason),
      sensitivity: sensitivity(item.sensitivity),
      priority: boundedFloat(item.priority),
      status: MemoirOpenQuestionStatus.OPEN,
    });
    openQuestions.push(openQuestion);
    await addEvidence(store, openid, projectId, turns, MemoirMemoryType.OPEN_QUESTION, openQuestion.id, item);
  }

  const summaryText = cleanText(payload.session_summary || payload.summary) || "本次访谈已完成结构化处理，暂无可用摘要。";
  const sessionSummary = await upsertSessionSummary(store, openid, projectId, session.id, {
    summary: summaryText,
    new_people_json: slimPeople(people),
    new_events_json: slimEvents(events),
    new_places_json: slimPlaces(places),
    new_themes_json: slimThemes(themes),
    open_questions_json: slimOpenQuestions(openQuestions),
    next_question_suggestions_json: Array.isArray(payload.next_question_suggestions) ? payload.next_question_suggestions : [],
  });

  let storyFragment = null;
  const storyText = cleanText(payload.story_fragment);
  if (storyText) {
    storyFragment = await upsertSessionOutputVersion(store, openid, projectId, session.id, {
      output_type: MemoirOutputType.STORY_FRAGMENT,
      title: cleanText(payload.story_title) || session.title || "访谈故事片段",
      content: storyText,
      model: extraction.model || null,
      prompt_snapshot: extraction.prompt || "",
    });
  }

  return {
    session_summary: sessionSummary,
    story_fragment: storyFragment,
    people,
    events,
    places,
    themes,
    open_questions: openQuestions,
  };
}

function containsAny(text, keywords) {
  const value = cleanText(text);
  return Boolean(value) && keywords.some((keyword) => keyword && value.includes(keyword));
}

function coverageStatus(score) {
  if (score >= 70) {
    return MemoirCoverageStatus.GREEN;
  }
  if (score >= 30) {
    return MemoirCoverageStatus.YELLOW;
  }
  return MemoirCoverageStatus.RED;
}

async function initializeDefaultCoverageTopics(store, openid, projectId) {
  const existing = await store.findMany(COLLECTIONS.coverageTopics, {
    openid,
    project_id: projectId,
  });
  const existingKeys = new Set(existing.map((topic) => topic.key));
  for (const [sortOrder, topic] of DEFAULT_COVERAGE_TOPICS.entries()) {
    if (existingKeys.has(topic.key)) {
      continue;
    }
    await store.insert(COLLECTIONS.coverageTopics, {
      openid,
      project_id: projectId,
      key: topic.key,
      name: topic.name,
      life_stage: topic.life_stage,
      description: topic.description,
      keywords_json: topic.keywords,
      sort_order: sortOrder,
      created_at: nowIso(),
      updated_at: nowIso(),
    });
  }
  return store.findMany(COLLECTIONS.coverageTopics, {
    openid,
    project_id: projectId,
  }, {
    orderBy: [{ field: "sort_order", direction: "asc" }],
  });
}

async function coverageSourceTexts(store, openid, projectId) {
  const rows = await getProjectRows(store, openid);
  const answerTexts = rows.questions.flatMap((question) => {
    const answer = rows.answers.find((item) => item.question_id === question.id);
    return answer ? [`${question.prompt} ${answer.transcript_raw || ""} ${answer.transcript_edited || ""}`] : [];
  });
  const people = await listMemoryRows(store, openid, projectId, MemoirMemoryType.PERSON);
  const events = await listMemoryRows(store, openid, projectId, MemoirMemoryType.EVENT);
  const places = await listMemoryRows(store, openid, projectId, MemoirMemoryType.PLACE);
  const themes = await listMemoryRows(store, openid, projectId, MemoirMemoryType.THEME);
  const memoryTexts = [
    ...people.map((item) => `${item.canonical_name} ${item.relationship || ""} ${item.description || ""}`),
    ...events.map((item) => `${item.title} ${item.life_stage || ""} ${item.time_text || ""} ${item.place_text || ""} ${item.process || ""} ${item.emotion || ""}`),
    ...places.map((item) => `${item.name} ${item.elder_alias || ""} ${item.modern_name || ""} ${item.description || ""}`),
    ...themes.map((item) => `${item.name} ${item.description || ""}`),
  ];
  const openQuestions = (await listOpenQuestions(store, openid))
    .filter((item) => item.status === MemoirOpenQuestionStatus.OPEN);
  const openQuestionTexts = openQuestions.map((item) => `${item.question} ${item.reason || ""}`);
  return { answerTexts, memoryTexts, openQuestionTexts };
}

async function calculateCoverageSnapshots(store, openid) {
  const project = await getOrCreateProject(store, openid);
  const topics = await initializeDefaultCoverageTopics(store, openid, project.id);
  const { answerTexts, memoryTexts, openQuestionTexts } = await coverageSourceTexts(store, openid, project.id);
  const snapshots = [];
  for (const topic of topics) {
    const keywords = parseJsonArray(topic.keywords_json);
    const answeredCount = answerTexts.filter((text) => containsAny(text, keywords)).length;
    const memoryCount = memoryTexts.filter((text) => containsAny(text, keywords)).length;
    const openQuestionCount = openQuestionTexts.filter((text) => containsAny(text, keywords)).length;
    const coverageScore = Math.min(100, answeredCount * 25 + memoryCount * 25);
    const status = coverageStatus(coverageScore);
    const snapshot = await store.insert(COLLECTIONS.coverageSnapshots, {
      openid,
      project_id: project.id,
      topic_id: topic.id,
      answered_count: answeredCount,
      memory_count: memoryCount,
      open_question_count: openQuestionCount,
      coverage_score: coverageScore,
      status,
      signals_json: {
        topic_key: topic.key,
        topic_name: topic.name,
        keywords,
        has_open_questions: openQuestionCount > 0,
      },
      calculated_at: nowIso(),
    });
    snapshots.push(snapshot);
  }
  return snapshots;
}

async function latestCoverageSnapshots(store, openid, projectId) {
  const topics = await store.findMany(COLLECTIONS.coverageTopics, {
    openid,
    project_id: projectId,
  }, {
    orderBy: [{ field: "sort_order", direction: "asc" }],
  });
  const snapshots = [];
  for (const topic of topics) {
    const latest = await store.findOne(COLLECTIONS.coverageSnapshots, {
      openid,
      topic_id: topic.id,
    }, {
      orderBy: [{ field: "calculated_at", direction: "desc" }],
    });
    if (latest) {
      snapshots.push(latest);
    }
  }
  return snapshots;
}

async function getCoverageMap(store, openid) {
  const project = await getOrCreateProject(store, openid);
  const topics = await store.findMany(COLLECTIONS.coverageTopics, {
    openid,
    project_id: project.id,
  }, {
    orderBy: [{ field: "sort_order", direction: "asc" }],
  });
  const topicById = new Map(topics.map((topic) => [topic.id, topic]));
  const snapshots = await latestCoverageSnapshots(store, openid, project.id);
  return snapshots.map((snapshot) => {
    const topic = topicById.get(snapshot.topic_id);
    if (!topic) {
      return null;
    }
    return {
      topic_id: topic.id,
      key: topic.key,
      name: topic.name,
      life_stage: topic.life_stage,
      description: topic.description,
      sort_order: topic.sort_order,
      answered_count: snapshot.answered_count,
      memory_count: snapshot.memory_count,
      open_question_count: snapshot.open_question_count,
      coverage_score: snapshot.coverage_score,
      status: snapshot.status,
      signals_json: parseJsonObject(snapshot.signals_json),
      calculated_at: snapshot.calculated_at,
    };
  }).filter(Boolean);
}

async function existingCandidateQuestions(store, openid, projectId) {
  const candidates = await store.findMany(COLLECTIONS.questionCandidates, {
    openid,
    project_id: projectId,
  });
  return new Set(candidates
    .filter((candidate) => [
      MemoirQuestionCandidateStatus.CANDIDATE,
      MemoirQuestionCandidateStatus.SELECTED,
    ].includes(candidate.status))
    .map((candidate) => candidate.question));
}

async function addCandidate(store, openid, projectId, data, existingQuestions) {
  const question = cleanText(data.question);
  if (!question || existingQuestions.has(question)) {
    return null;
  }
  const candidate = await store.insert(COLLECTIONS.questionCandidates, {
    openid,
    project_id: projectId,
    session_id: data.session_id || null,
    question,
    question_type: questionType(data.question_type || "depth", "depth"),
    target_memory_type: data.target_memory_type || null,
    target_memory_id: data.target_memory_id || null,
    coverage_topic_id: data.coverage_topic_id || null,
    reason: cleanText(data.reason),
    sensitivity: sensitivity(data.sensitivity),
    stage: data.stage || "depth",
    priority: Math.max(0, Math.min(1, Number(data.priority) || 0)),
    status: MemoirQuestionCandidateStatus.CANDIDATE,
    created_by: MemoirCreatedBy.AI,
    created_at: nowIso(),
  });
  existingQuestions.add(question);
  return candidate;
}

async function generateQuestionCandidates(store, openid) {
  const project = await getOrCreateProject(store, openid);
  const state = await getOrCreateCurrentSession(store, openid);
  const sessionId = state.session.id;
  const existingQuestions = await existingCandidateQuestions(store, openid, project.id);
  const candidates = [];
  const openQuestions = (await listOpenQuestions(store, openid))
    .filter((item) => item.status === MemoirOpenQuestionStatus.OPEN);
  for (const openQuestion of openQuestions) {
    const priority = openQuestion.sensitivity === "high"
      ? Math.min(Number(openQuestion.priority) || 0.62, 0.62)
      : Number(openQuestion.priority) || 0.72;
    const candidate = await addCandidate(store, openid, project.id, {
      session_id: sessionId,
      question: openQuestion.question,
      question_type: openQuestion.question_type,
      target_memory_type: MemoirMemoryType.OPEN_QUESTION,
      target_memory_id: openQuestion.id,
      reason: openQuestion.reason || "待确认问题需要继续追问",
      sensitivity: openQuestion.sensitivity,
      stage: "depth",
      priority,
    }, existingQuestions);
    if (candidate) {
      candidates.push(candidate);
    }
  }

  const events = await listMemoryRows(store, openid, project.id, MemoirMemoryType.EVENT);
  for (const event of events) {
    if (!cleanText(event.time_text)) {
      const candidate = await addCandidate(store, openid, project.id, {
        session_id: sessionId,
        question: `你刚才说的「${event.title}」，大概是在什么时候？`,
        question_type: "clarification",
        target_memory_type: MemoirMemoryType.EVENT,
        target_memory_id: event.id,
        reason: "关键事件缺少时间信息",
        priority: 0.78,
      }, existingQuestions);
      if (candidate) candidates.push(candidate);
    }
    if (!cleanText(event.place_text)) {
      const candidate = await addCandidate(store, openid, project.id, {
        session_id: sessionId,
        question: `「${event.title}」这件事主要发生在哪里？`,
        question_type: "clarification",
        target_memory_type: MemoirMemoryType.EVENT,
        target_memory_id: event.id,
        reason: "关键事件缺少地点信息",
        priority: 0.76,
      }, existingQuestions);
      if (candidate) candidates.push(candidate);
    }
    if (!cleanText(event.process)) {
      const candidate = await addCandidate(store, openid, project.id, {
        session_id: sessionId,
        question: `「${event.title}」当时具体是怎么发生的？`,
        question_type: "depth",
        target_memory_type: MemoirMemoryType.EVENT,
        target_memory_id: event.id,
        reason: "关键事件缺少经过细节",
        priority: 0.7,
      }, existingQuestions);
      if (candidate) candidates.push(candidate);
    }
  }

  const snapshots = await calculateCoverageSnapshots(store, openid);
  const topics = await store.findMany(COLLECTIONS.coverageTopics, {
    openid,
    project_id: project.id,
  });
  const topicById = new Map(topics.map((topic) => [topic.id, topic]));
  const hasSignal = snapshots.some((snapshot) => snapshot.answered_count || snapshot.memory_count || snapshot.open_question_count);
  if (hasSignal) {
    for (const snapshot of snapshots) {
      if (snapshot.status === MemoirCoverageStatus.GREEN) {
        continue;
      }
      const topic = topicById.get(snapshot.topic_id);
      if (!topic) {
        continue;
      }
      const candidate = await addCandidate(store, openid, project.id, {
        session_id: sessionId,
        question: `关于「${topic.name}」，你愿意多讲一件现在还记得的事吗？`,
        question_type: "depth",
        coverage_topic_id: topic.id,
        reason: "人生覆盖度较低，需要补足基础材料",
        stage: "skeleton",
        priority: 0.45 + (100 - snapshot.coverage_score) / 400,
      }, existingQuestions);
      if (candidate) candidates.push(candidate);
    }
  }
  return candidates;
}

async function listQuestionCandidates(store, openid) {
  const project = await getOrCreateProject(store, openid);
  return store.findMany(COLLECTIONS.questionCandidates, {
    openid,
    project_id: project.id,
    status: MemoirQuestionCandidateStatus.CANDIDATE,
  }, {
    orderBy: [{ field: "priority", direction: "desc" }, { field: "created_at", direction: "asc" }],
  });
}

async function selectCandidateAsQuestion(store, openid, candidateId) {
  const candidate = await store.findOne(COLLECTIONS.questionCandidates, {
    openid,
    _id: candidateId,
  });
  if (!candidate) {
    throw new ApiError(404, "问题候选不存在");
  }
  if (candidate.status !== MemoirQuestionCandidateStatus.CANDIDATE) {
    throw new ApiError(409, "问题候选当前不可选择");
  }
  if (!candidate.session_id) {
    throw new ApiError(400, "问题候选未关联访谈");
  }
  const existingQuestion = await store.findOne(COLLECTIONS.questions, {
    openid,
    session_id: candidate.session_id,
    prompt: candidate.question,
    status: MemoirQuestionStatus.PENDING,
  });
  let question = existingQuestion;
  if (!question) {
    const questions = await getOrderedQuestions(store, openid, candidate.session_id);
    const sortOrder = questions.reduce((max, item) => Math.max(max, Number(item.sort_order) || 0), 0) + 1;
    question = await store.insert(COLLECTIONS.questions, {
      openid,
      session_id: candidate.session_id,
      prompt: candidate.question,
      source: "ai_generated",
      sort_order: sortOrder,
      status: MemoirQuestionStatus.PENDING,
      created_at: nowIso(),
    });
  }
  await store.updateById(COLLECTIONS.questionCandidates, candidate.id, {
    status: MemoirQuestionCandidateStatus.SELECTED,
  });
  if (candidate.target_memory_type === MemoirMemoryType.OPEN_QUESTION && candidate.target_memory_id) {
    await store.updateById(COLLECTIONS.openQuestions, candidate.target_memory_id, {
      status: MemoirOpenQuestionStatus.ASKED,
      updated_at: nowIso(),
    });
  }
  await store.updateById(COLLECTIONS.sessions, candidate.session_id, {
    current_question_id: question.id,
    status: MemoirSessionStatus.ACTIVE,
    updated_at: nowIso(),
  });
  return question;
}

async function getOrCreatePlannedQuestion(store, openid, sessionId) {
  const existing = await store.findOne(COLLECTIONS.questions, {
    openid,
    session_id: sessionId,
    source: "ai_generated",
    status: MemoirQuestionStatus.PENDING,
  });
  if (existing) {
    await store.updateById(COLLECTIONS.sessions, sessionId, {
      current_question_id: existing.id,
      status: MemoirSessionStatus.ACTIVE,
      updated_at: nowIso(),
    });
    return existing;
  }
  await generateQuestionCandidates(store, openid);
  const candidates = await listQuestionCandidates(store, openid);
  const sessionCandidates = candidates.filter((candidate) => candidate.session_id === sessionId);
  if (!sessionCandidates.length) {
    return null;
  }
  const nonHigh = sessionCandidates.filter((candidate) => candidate.sensitivity !== "high");
  const eligible = nonHigh.length ? nonHigh : sessionCandidates;
  const best = eligible.sort((left, right) => {
    if (right.priority !== left.priority) {
      return right.priority - left.priority;
    }
    return String(left.created_at).localeCompare(String(right.created_at));
  })[0];
  return selectCandidateAsQuestion(store, openid, best.id);
}

async function advanceSession(store, openid, sessionId) {
  const session = await loadSession(store, openid, sessionId);
  const questions = await getOrderedQuestions(store, openid, sessionId);
  const currentPendingQuestion = questions.find((question) => (
    question.id === session.current_question_id
    && question.status === MemoirQuestionStatus.PENDING
  ));
  const plannedQuestion = questions.find((question) => (
    question.source === "ai_generated"
    && question.status === MemoirQuestionStatus.PENDING
  ));
  const nextQuestion = currentPendingQuestion
    || plannedQuestion
    || questions.find((question) => question.status === MemoirQuestionStatus.PENDING)
    || null;
  const status = nextQuestion ? MemoirSessionStatus.ACTIVE : MemoirSessionStatus.COMPLETED;
  return store.updateById(COLLECTIONS.sessions, sessionId, {
    current_question_id: nextQuestion ? nextQuestion.id : null,
    status,
    updated_at: nowIso(),
  });
}

async function getSessionState(store, openid, sessionId) {
  const session = await loadSession(store, openid, sessionId);
  const advancedSession = await advanceSession(store, openid, session.id);
  const questions = await getOrderedQuestions(store, openid, session.id);
  const currentQuestion = questions.find((question) => question.id === advancedSession.current_question_id) || null;
  const completedStatuses = new Set([
    MemoirQuestionStatus.ANSWERED,
    MemoirQuestionStatus.SKIPPED,
  ]);
  const answers = await store.findMany(COLLECTIONS.answers, {
    openid,
  });
  const latestDraft = await getLatestDraft(
    store,
    openid,
    answers.filter((answer) => questions.some((question) => question.id === answer.question_id)),
  );

  return {
    session: advancedSession,
    current_question: currentQuestion,
    completed_question_count: questions.filter((question) => completedStatuses.has(question.status)).length,
    pending_question_count: questions.filter((question) => question.status === MemoirQuestionStatus.PENDING).length,
    latest_draft: latestDraft,
    questions,
  };
}

async function getNextQuestion(store, openid) {
  const state = await getOrCreateCurrentSession(store, openid);
  const plannedQuestion = await getOrCreatePlannedQuestion(store, openid, state.session.id);
  return plannedQuestion || state.current_question;
}

async function skipQuestion(store, openid, questionId) {
  const question = await loadQuestion(store, openid, questionId);
  if (question.status === MemoirQuestionStatus.ANSWERED) {
    throw new ApiError(409, "已回答的问题不能跳过");
  }

  await store.updateById(COLLECTIONS.questions, question.id, {
    status: MemoirQuestionStatus.SKIPPED,
  });
  return getSessionState(store, openid, question.session_id);
}

async function answerAudioPatch(store, openid, data) {
  if (!data.audio_file_id) {
    return {};
  }

  const audioFile = await loadAudioFile(store, openid, data.audio_file_id);
  return {
    audio_file_id: audioFile.id,
    duration_seconds: cleanPositiveNumber(data.duration_seconds, audioFile.duration_seconds),
  };
}

async function createTextAnswer(store, openid, data) {
  const question = await loadQuestion(store, openid, data.question_id);
  if (question.status === MemoirQuestionStatus.SKIPPED) {
    throw new ApiError(409, "已跳过的问题不能回答");
  }

  const transcript = cleanAnswerText(data.text);
  const audioPatch = await answerAudioPatch(store, openid, data);
  const now = nowIso();
  const existing = await store.findOne(COLLECTIONS.answers, {
    openid,
    question_id: question.id,
  });

  const answer = existing
    ? await store.updateById(COLLECTIONS.answers, existing.id, {
      transcript_raw: existing.transcript_raw || transcript,
      transcript_edited: transcript,
      status: MemoirAnswerStatus.ANSWERED,
      updated_at: now,
      ...audioPatch,
    })
    : await store.insert(COLLECTIONS.answers, {
      openid,
      question_id: question.id,
      audio_file_id: audioPatch.audio_file_id || null,
      transcript_raw: transcript,
      transcript_edited: transcript,
      status: MemoirAnswerStatus.ANSWERED,
      duration_seconds: audioPatch.duration_seconds ?? null,
      created_at: now,
      updated_at: now,
    });

  await store.updateById(COLLECTIONS.questions, question.id, {
    status: MemoirQuestionStatus.ANSWERED,
  });
  await advanceSession(store, openid, question.session_id);
  return answer;
}

async function updateAnswerText(store, openid, answerId, data) {
  const answer = await loadAnswer(store, openid, answerId);
  const text = cleanAnswerText(data.text);
  const updated = await store.updateById(COLLECTIONS.answers, answer.id, {
    transcript_edited: text,
    status: MemoirAnswerStatus.ANSWERED,
    updated_at: nowIso(),
  });

  const question = await loadQuestion(store, openid, updated.question_id);
  await store.updateById(COLLECTIONS.questions, question.id, {
    status: MemoirQuestionStatus.ANSWERED,
  });
  await advanceSession(store, openid, question.session_id);
  return updated;
}

async function registerAudioFile(store, openid, data) {
  const now = nowIso();
  const cloudPath = cleanCloudAudioPath(openid, data.cloud_path);
  const extension = audioExtension(cloudPath);
  const audioFile = await store.insert(COLLECTIONS.audioFiles, {
    openid,
    file_id: cleanCloudFileId(data.file_id, cloudPath),
    cloud_path: cloudPath,
    mime_type: cleanAudioMimeType(data.mime_type, extension),
    size_bytes: cleanAudioSizeBytes(data.size_bytes),
    duration_seconds: cleanPositiveNumber(data.duration_seconds),
    transcript_text: "",
    transcript_usage_json: "",
    transcribed_at: null,
    created_at: now,
  });

  return {
    id: audioFile.id,
    mime_type: audioFile.mime_type,
    size_bytes: audioFile.size_bytes,
    duration_seconds: audioFile.duration_seconds,
    created_at: audioFile.created_at,
  };
}

async function loadAudioFile(store, openid, audioFileId) {
  const audioFile = await store.findOne(COLLECTIONS.audioFiles, {
    openid,
    _id: audioFileId,
  });
  if (!audioFile) {
    throw new ApiError(404, "音频文件不存在");
  }
  return audioFile;
}

async function transcribeAudioFile(store, openid, audioFileId, dependencies) {
  if (!dependencies.storage?.downloadFile) {
    throw new ApiError(500, "云存储未配置");
  }
  if (!dependencies.asr?.transcribe) {
    throw new ApiError(500, "语音转写服务未配置");
  }

  const audioFile = await loadAudioFile(store, openid, audioFileId);
  const audioBytes = await dependencies.storage.downloadFile(audioFile.file_id);
  const result = await dependencies.asr.transcribe(audioBytes, audioFile);
  await store.updateById(COLLECTIONS.audioFiles, audioFile.id, {
    transcript_text: result.text,
    transcript_usage_json: serializeJsonField(result.usage),
    transcribed_at: nowIso(),
  });

  return {
    audio_file_id: audioFile.id,
    text: result.text,
    usage: result.usage || null,
  };
}

async function generateDraft(store, openid, answerId, dependencies) {
  if (!dependencies.drafting?.generate) {
    throw new ApiError(500, "AI 整理服务未配置");
  }

  const answer = await loadAnswer(store, openid, answerId);
  if (answer.status === MemoirAnswerStatus.CONFIRMED) {
    throw new ApiError(409, "已确认的回答不能重新整理");
  }

  const question = await loadQuestion(store, openid, answer.question_id);
  const generated = await dependencies.drafting.generate({
    questionPrompt: question.prompt,
    answerText: answerText(answer),
  });
  const draftText = cleanDraftText(generated.text);
  const now = nowIso();
  const existing = await store.findOne(COLLECTIONS.drafts, {
    openid,
    answer_id: answer.id,
  });

  const draft = existing
    ? await store.updateById(COLLECTIONS.drafts, existing.id, {
      content_raw: draftText,
      content_edited: draftText,
      status: MemoirDraftStatus.DRAFT,
      updated_at: now,
    })
    : await store.insert(COLLECTIONS.drafts, {
      openid,
      answer_id: answer.id,
      content_raw: draftText,
      content_edited: draftText,
      status: MemoirDraftStatus.DRAFT,
      created_at: now,
      updated_at: now,
    });

  await store.updateById(COLLECTIONS.answers, answer.id, {
    status: MemoirAnswerStatus.DRAFTED,
    updated_at: now,
  });
  return draft;
}

async function updateDraftContent(store, openid, draftId, data) {
  const draft = await loadDraft(store, openid, draftId);
  if (draft.status === MemoirDraftStatus.ACCEPTED) {
    throw new ApiError(409, "已确认的整理稿不能编辑");
  }

  return store.updateById(COLLECTIONS.drafts, draft.id, {
    content_edited: cleanDraftText(data.content_edited),
    updated_at: nowIso(),
  });
}

async function acceptDraft(store, openid, draftId) {
  const draft = await loadDraft(store, openid, draftId);
  cleanDraftText(draft.content_edited);
  const now = nowIso();
  const acceptedDraft = await store.updateById(COLLECTIONS.drafts, draft.id, {
    status: MemoirDraftStatus.ACCEPTED,
    updated_at: now,
  });
  await store.updateById(COLLECTIONS.answers, draft.answer_id, {
    status: MemoirAnswerStatus.CONFIRMED,
    updated_at: now,
  });
  return acceptedDraft;
}

async function listOutputs(store, openid) {
  const project = await getOrCreateProject(store, openid);
  return store.findMany(COLLECTIONS.outputs, {
    openid,
    project_id: project.id,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });
}

async function getOutput(store, openid, outputId) {
  const project = await getOrCreateProject(store, openid);
  const output = await store.findOne(COLLECTIONS.outputs, {
    openid,
    project_id: project.id,
    _id: outputId,
  });
  if (!output) {
    throw new ApiError(404, "写作产物不存在");
  }
  const versions = await store.findMany(COLLECTIONS.outputVersions, {
    openid,
    output_id: output.id,
  }, {
    orderBy: [{ field: "version", direction: "asc" }],
  });
  return {
    ...output,
    versions,
  };
}

function createMemoirService(store, context, dependencies = {}) {
  const openid = requireOpenId(context);

  return {
    getProject: async () => createProjectResponse(await getOrCreateProject(store, openid)),
    getCurrentSession: async () => getOrCreateCurrentSession(store, openid),
    getNextQuestion: async () => getNextQuestion(store, openid),
    getSummary: async () => getSummary(store, openid),
    listStoryCards: async () => listStoryCards(store, openid),
    getStoryDetail: async (answerId) => getStoryDetail(store, openid, answerId),
    getSessionSummary: async (sessionId) => getSessionSummary(store, openid, sessionId),
    processSessionMemory: async (sessionId) => processSessionMemory(store, openid, sessionId, dependencies),
    listPersonMemories: async () => listMemoryRows(store, openid, (await getOrCreateProject(store, openid)).id, MemoirMemoryType.PERSON),
    listEventMemories: async () => listMemoryRows(store, openid, (await getOrCreateProject(store, openid)).id, MemoirMemoryType.EVENT),
    listPlaceMemories: async () => listMemoryRows(store, openid, (await getOrCreateProject(store, openid)).id, MemoirMemoryType.PLACE),
    listThemeMemories: async () => listMemoryRows(store, openid, (await getOrCreateProject(store, openid)).id, MemoirMemoryType.THEME),
    updateMemory: async (memoryType, memoryId, data) => updateMemory(store, openid, memoryType, memoryId, data),
    listOpenQuestions: async () => listOpenQuestions(store, openid),
    updateOpenQuestion: async (questionId, data) => updateOpenQuestion(store, openid, questionId, data),
    getCoverageMap: async () => getCoverageMap(store, openid),
    recalculateCoverage: async () => {
      await calculateCoverageSnapshots(store, openid);
      return getCoverageMap(store, openid);
    },
    listQuestionCandidates: async () => listQuestionCandidates(store, openid),
    generateQuestionCandidates: async () => {
      await generateQuestionCandidates(store, openid);
      return listQuestionCandidates(store, openid);
    },
    selectCandidateAsQuestion: async (candidateId) => selectCandidateAsQuestion(store, openid, candidateId),
    listOutputs: async () => listOutputs(store, openid),
    getOutput: async (outputId) => getOutput(store, openid, outputId),
    skipQuestion: async (questionId) => skipQuestion(store, openid, questionId),
    createTextAnswer: async (data) => createTextAnswer(store, openid, data),
    updateAnswerText: async (answerId, data) => updateAnswerText(store, openid, answerId, data),
    registerAudioFile: async (data) => registerAudioFile(store, openid, data),
    transcribeAudioFile: async (audioFileId) => transcribeAudioFile(store, openid, audioFileId, dependencies),
    generateDraft: async (answerId) => generateDraft(store, openid, answerId, dependencies),
    updateDraftContent: async (draftId, data) => updateDraftContent(store, openid, draftId, data),
    acceptDraft: async (draftId) => acceptDraft(store, openid, draftId),
  };
}

module.exports = {
  createMemoirService,
  MemoirAnswerStatus,
  MemoirDraftStatus,
  MemoirQuestionStatus,
  MemoirSessionStatus,
};
