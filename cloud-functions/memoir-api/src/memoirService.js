const { ApiError } = require("./http");
const { COLLECTIONS } = require("./collections");
const { textHash } = require("./tts/stepfun");
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

const MemoirAnswerSessionStatus = Object.freeze({
  DRAFT: "draft",
  SUBMITTED: "submitted",
});

const MemoirAnswerSegmentStatus = Object.freeze({
  DRAFT: "draft",
  UPLOADED: "uploaded",
  TRANSCRIBING: "transcribing",
  TRANSCRIBED: "transcribed",
  FAILED: "failed",
});

const MemoirAsrJobStatus = Object.freeze({
  QUEUED: "queued",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
});

const MemoirTtsJobStatus = Object.freeze({
  QUEUED: "queued",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
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
const DEFAULT_MEMOIR_QUESTION_PROMPTS = new Set(DEFAULT_MEMOIR_QUESTIONS);
const MAX_AUDIO_BYTES = 20 * 1024 * 1024;
const CLOUD_AUDIO_ROOT = "memoir/audio";
const CLOUD_TTS_ROOT = "memoir/tts";
const DEFAULT_TTS_MODEL = "stepaudio-2.5-tts";
const DEFAULT_TTS_VOICE = "cixingnansheng";
const DEFAULT_TTS_FORMAT = "mp3";
const DEFAULT_TTS_SPEED = 1;
const TTS_MIME_BY_FORMAT = Object.freeze({
  mp3: "audio/mpeg",
});
const DEFAULT_WECHAT_DISPLAY_NAME = "微信用户";
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
    user_id: project.user_id || null,
    type: "memoir",
    title: project.title,
    description: project.description || "",
    total_word_count: project.total_word_count || 0,
    status: project.status || "active",
    elder_name: project.elder_name || "",
    is_default: Boolean(project.is_default),
    last_opened_at: project.last_opened_at || project.updated_at || project.created_at,
    groups: [],
    references: [],
    created_at: project.created_at,
    updated_at: project.updated_at,
  };
}

function createUserResponse(user) {
  return {
    id: user.id,
    openid: user.openid,
    unionid: user.unionid || "",
    email: "",
    display_name: user.display_name || user.wechat_nickname || DEFAULT_WECHAT_DISPLAY_NAME,
    wechat_nickname: user.wechat_nickname || "",
    wechat_avatar_url: user.wechat_avatar_url || "",
    storage_owner_id: safePathSegment(user.openid),
    active_project_id: user.active_project_id || null,
    created_at: user.created_at,
    updated_at: user.updated_at,
    last_login_at: user.last_login_at || user.updated_at,
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

function cleanNonNegativeInteger(value, fallback = 0) {
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

function cleanOptionalText(value) {
  return String(value || "").trim();
}

function defaultProjectTitle(user) {
  return `${user.display_name || user.wechat_nickname || DEFAULT_WECHAT_DISPLAY_NAME}的回忆录`;
}

async function getOrCreateMemoirUser(store, openid, data = {}) {
  const now = nowIso();
  const existing = await store.findOne(COLLECTIONS.users, { openid });
  const wechatNickname = cleanOptionalText(data.wechat_nickname || data.nickname || data.nickName);
  const wechatAvatarUrl = cleanOptionalText(data.wechat_avatar_url || data.avatar_url || data.avatarUrl);
  const displayName = cleanOptionalText(data.display_name) || wechatNickname;
  const unionid = cleanOptionalText(data.unionid);

  if (existing) {
    const patch = {
      last_login_at: now,
      updated_at: now,
    };
    if (unionid) {
      patch.unionid = unionid;
    }
    if (wechatNickname) {
      patch.wechat_nickname = wechatNickname;
      patch.display_name = displayName || existing.display_name || wechatNickname;
    } else if (displayName) {
      patch.display_name = displayName;
    }
    if (wechatAvatarUrl) {
      patch.wechat_avatar_url = wechatAvatarUrl;
    }
    return store.updateById(COLLECTIONS.users, existing.id, patch);
  }

  return store.insert(COLLECTIONS.users, {
    openid,
    unionid: unionid || null,
    wechat_nickname: wechatNickname || "",
    wechat_avatar_url: wechatAvatarUrl || "",
    display_name: displayName || DEFAULT_WECHAT_DISPLAY_NAME,
    active_project_id: null,
    created_at: now,
    updated_at: now,
    last_login_at: now,
  });
}

async function listUserProjects(store, openid, user) {
  const rows = await store.findMany(COLLECTIONS.projects, {
    openid,
    type: "memoir",
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });

  const projects = [];
  for (const [index, row] of rows.entries()) {
    if (!row.user_id || row.status === undefined || row.is_default === undefined) {
      projects.push(await store.updateById(COLLECTIONS.projects, row.id, {
        user_id: user.id,
        status: row.status || "active",
        elder_name: row.elder_name || "",
        is_default: row.is_default === undefined ? index === 0 : Boolean(row.is_default),
        last_opened_at: row.last_opened_at || row.updated_at || row.created_at,
        updated_at: row.updated_at || nowIso(),
      }));
      continue;
    }
    projects.push(row);
  }
  return projects.filter(Boolean);
}

async function createMemoirProject(store, openid, data = {}) {
  const user = await getOrCreateMemoirUser(store, openid);
  const existing = await listUserProjects(store, openid, user);
  const now = nowIso();
  const title = cleanOptionalText(data.title) || defaultProjectTitle(user);
  const project = await store.insert(COLLECTIONS.projects, {
    openid,
    user_id: user.id,
    type: "memoir",
    title,
    description: cleanOptionalText(data.description),
    total_word_count: 0,
    status: "active",
    elder_name: cleanOptionalText(data.elder_name),
    is_default: existing.length === 0,
    last_opened_at: now,
    created_at: now,
    updated_at: now,
  });
  await store.updateById(COLLECTIONS.users, user.id, {
    active_project_id: project.id,
    updated_at: now,
  });
  return project;
}

async function renameDefaultProjectForUser(store, openid, user) {
  const nextTitle = defaultProjectTitle(user);
  const genericTitle = `${DEFAULT_WECHAT_DISPLAY_NAME}的回忆录`;
  const projects = await listUserProjects(store, openid, user);
  const defaultProject = projects.find((project) => project.is_default) || projects[0];
  if (!defaultProject || defaultProject.title !== genericTitle || nextTitle === genericTitle) {
    return null;
  }
  return store.updateById(COLLECTIONS.projects, defaultProject.id, {
    title: nextTitle,
    updated_at: nowIso(),
  });
}

async function getActiveProject(store, openid, user) {
  let projects = await listUserProjects(store, openid, user);
  if (!projects.length) {
    const project = await createMemoirProject(store, openid);
    projects = [project];
  }
  const activeProject = projects.find((project) => project.id === user.active_project_id)
    || projects.find((project) => project.is_default)
    || projects[0];

  if (!user.active_project_id || user.active_project_id !== activeProject.id) {
    await store.updateById(COLLECTIONS.users, user.id, {
      active_project_id: activeProject.id,
      updated_at: nowIso(),
    });
  }
  return activeProject;
}

async function getOrCreateProject(store, openid) {
  const user = await getOrCreateMemoirUser(store, openid);
  return getActiveProject(store, openid, user);
}

async function updateProjectScopedRow(store, collection, row, projectId, patch = {}) {
  const nextPatch = { ...patch };
  if (projectId && !row.project_id) {
    nextPatch.project_id = projectId;
  }
  const entries = Object.entries(nextPatch)
    .filter(([, value]) => value !== undefined);
  if (!entries.length) {
    return row;
  }
  return store.updateById(collection, row.id, Object.fromEntries(entries));
}

async function getMemoirIdentity(store, openid) {
  const user = await getOrCreateMemoirUser(store, openid);
  const activeProject = await getActiveProject(store, openid, user);
  const projects = await listUserProjects(store, openid, user);
  const freshUser = await store.getById(COLLECTIONS.users, user.id) || user;
  return {
    ...createUserResponse(freshUser),
    active_project_id: activeProject.id,
    active_project: createProjectResponse(activeProject),
    projects: projects.map(createProjectResponse),
  };
}

async function loadUserProject(store, openid, projectId) {
  const user = await getOrCreateMemoirUser(store, openid);
  const project = await store.findOne(COLLECTIONS.projects, {
    openid,
    _id: projectId,
  });
  if (!project || (project.user_id && project.user_id !== user.id)) {
    throw new ApiError(404, "回忆录不存在");
  }
  if (!project.user_id) {
    return store.updateById(COLLECTIONS.projects, project.id, {
      user_id: user.id,
      updated_at: nowIso(),
    });
  }
  return project;
}

async function updateMemoirProject(store, openid, projectId, data = {}) {
  const project = await loadUserProject(store, openid, projectId);
  const patch = {
    updated_at: nowIso(),
  };
  if (data.title !== undefined) {
    patch.title = cleanRequiredText(data.title, "回忆录名称不能为空");
  }
  if (data.description !== undefined) {
    patch.description = cleanOptionalText(data.description);
  }
  if (data.elder_name !== undefined) {
    patch.elder_name = cleanOptionalText(data.elder_name);
  }
  return store.updateById(COLLECTIONS.projects, project.id, patch);
}

async function activateMemoirProject(store, openid, projectId) {
  const user = await getOrCreateMemoirUser(store, openid);
  const project = await loadUserProject(store, openid, projectId);
  const now = nowIso();
  const updatedProject = await store.updateById(COLLECTIONS.projects, project.id, {
    last_opened_at: now,
    updated_at: now,
  });
  await store.updateById(COLLECTIONS.users, user.id, {
    active_project_id: project.id,
    updated_at: now,
  });
  return updatedProject;
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
  await syncSessionTemplateQuestions(store, openid, session);
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
      project_id: projectId,
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

async function syncSessionTemplateQuestions(store, openid, session) {
  const questions = await getOrderedQuestions(store, openid, session.id, session.project_id);
  const existingDefaultPrompts = new Set();
  const removedQuestionIds = new Set();

  for (const question of questions) {
    if (question.source !== "template") {
      continue;
    }
    if (DEFAULT_MEMOIR_QUESTION_PROMPTS.has(question.prompt)) {
      existingDefaultPrompts.add(question.prompt);
      const sortOrder = DEFAULT_MEMOIR_QUESTIONS.indexOf(question.prompt);
      const patch = {};
      if (question.sort_order !== sortOrder) {
        patch.sort_order = sortOrder;
      }
      if (question.status === MemoirQuestionStatus.SKIPPED) {
        patch.status = MemoirQuestionStatus.PENDING;
      }
      if (Object.keys(patch).length) {
        await store.updateById(COLLECTIONS.questions, question.id, patch);
      }
      continue;
    }
    if (question.status === MemoirQuestionStatus.ANSWERED) {
      continue;
    }
    await store.deleteById(COLLECTIONS.questions, question.id);
    removedQuestionIds.add(question.id);
  }

  for (const [index, prompt] of DEFAULT_MEMOIR_QUESTIONS.entries()) {
    if (existingDefaultPrompts.has(prompt)) {
      continue;
    }
    await store.insert(COLLECTIONS.questions, {
      openid,
      project_id: session.project_id,
      session_id: session.id,
      prompt,
      source: "template",
      sort_order: index,
      status: MemoirQuestionStatus.PENDING,
      created_at: nowIso(),
    });
  }

  if (session.current_question_id && removedQuestionIds.has(session.current_question_id)) {
    await store.updateById(COLLECTIONS.sessions, session.id, {
      current_question_id: null,
      status: MemoirSessionStatus.ACTIVE,
      updated_at: nowIso(),
    });
  }
}

async function loadSession(store, openid, sessionId, projectId = null) {
  const session = await store.findOne(COLLECTIONS.sessions, {
    openid,
    _id: sessionId,
  });
  if (!session || (projectId && session.project_id !== projectId)) {
    throw new ApiError(404, "访谈会话不存在");
  }
  return session;
}

async function loadQuestion(store, openid, questionId, projectId = null) {
  const question = await store.findOne(COLLECTIONS.questions, {
    openid,
    _id: questionId,
  });
  if (!question) {
    throw new ApiError(404, "问题不存在");
  }
  if (!projectId) {
    return question;
  }
  if (question.project_id && question.project_id !== projectId) {
    throw new ApiError(404, "问题不存在");
  }
  if (!question.project_id) {
    const session = await loadSession(store, openid, question.session_id, projectId);
    return updateProjectScopedRow(store, COLLECTIONS.questions, question, projectId, {
      session_id: session.id,
    });
  }
  return question;
}

async function loadAnswer(store, openid, answerId, projectId = null) {
  const answer = await store.findOne(COLLECTIONS.answers, {
    openid,
    _id: answerId,
  });
  if (!answer) {
    throw new ApiError(404, "回答不存在");
  }
  if (!projectId) {
    return answer;
  }
  if (answer.project_id && answer.project_id !== projectId) {
    throw new ApiError(404, "回答不存在");
  }
  if (!answer.project_id) {
    const question = await loadQuestion(store, openid, answer.question_id, projectId);
    return updateProjectScopedRow(store, COLLECTIONS.answers, answer, projectId, {
      session_id: answer.session_id || question.session_id,
    });
  }
  return answer;
}

async function loadAnswerSession(store, openid, answerSessionId, projectId = null) {
  const answerSession = await store.findOne(COLLECTIONS.answerSessions, {
    openid,
    _id: answerSessionId,
  });
  if (!answerSession) {
    throw new ApiError(404, "回答会话不存在");
  }
  if (projectId && answerSession.project_id !== projectId) {
    throw new ApiError(404, "回答会话不存在");
  }
  return answerSession;
}

async function loadAnswerSegment(store, openid, answerSegmentId, projectId = null) {
  const segment = await store.findOne(COLLECTIONS.answerSegments, {
    openid,
    _id: answerSegmentId,
  });
  if (!segment) {
    throw new ApiError(404, "回答分段不存在");
  }
  if (projectId && segment.project_id !== projectId) {
    throw new ApiError(404, "回答分段不存在");
  }
  return segment;
}

async function loadAsrJob(store, openid, asrJobId, projectId = null) {
  const job = await store.findOne(COLLECTIONS.asrJobs, {
    openid,
    _id: asrJobId,
  });
  if (!job) {
    throw new ApiError(404, "转写任务不存在");
  }
  if (projectId && job.project_id !== projectId) {
    throw new ApiError(404, "转写任务不存在");
  }
  return job;
}

async function loadDraft(store, openid, draftId, projectId = null) {
  const draft = await store.findOne(COLLECTIONS.drafts, {
    openid,
    _id: draftId,
  });
  if (!draft) {
    throw new ApiError(404, "整理稿不存在");
  }
  if (!projectId) {
    return draft;
  }
  if (draft.project_id && draft.project_id !== projectId) {
    throw new ApiError(404, "整理稿不存在");
  }
  if (!draft.project_id) {
    const answer = await loadAnswer(store, openid, draft.answer_id, projectId);
    return updateProjectScopedRow(store, COLLECTIONS.drafts, draft, projectId, {
      session_id: draft.session_id || answer.session_id,
      question_id: draft.question_id || answer.question_id,
    });
  }
  return draft;
}

async function getOrderedQuestions(store, openid, sessionId, projectId = null) {
  const rows = await store.findMany(COLLECTIONS.questions, {
    openid,
    session_id: sessionId,
  }, {
    orderBy: [{ field: "sort_order", direction: "asc" }],
  });
  if (!projectId) {
    return rows;
  }
  const questions = [];
  for (const question of rows) {
    if (question.project_id && question.project_id !== projectId) {
      continue;
    }
    questions.push(await updateProjectScopedRow(store, COLLECTIONS.questions, question, projectId));
  }
  return questions;
}

async function getLatestDraft(store, openid, answers, projectId = null) {
  const drafts = [];
  for (const answer of answers) {
    const draft = await store.findOne(COLLECTIONS.drafts, {
      openid,
      answer_id: answer.id,
    }, {
      orderBy: [{ field: "updated_at", direction: "desc" }],
    });
    if (draft) {
      if (projectId && draft.project_id && draft.project_id !== projectId) {
        continue;
      }
      drafts.push(await updateProjectScopedRow(store, COLLECTIONS.drafts, draft, projectId, {
        session_id: draft.session_id || answer.session_id,
        question_id: draft.question_id || answer.question_id,
      }));
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
  const rows = await store.findMany(memoryCollection(memoryType), {
    openid,
    project_id: projectId,
  }, {
    orderBy: [{ field: "created_at", direction: "asc" }],
  });
  const enriched = [];
  for (const row of rows) {
    const evidenceRows = await store.findMany(COLLECTIONS.memoryEvidence, {
      openid,
      project_id: projectId,
      memory_type: memoryType,
      memory_id: row.id,
    }, {
      orderBy: [{ field: "created_at", direction: "asc" }],
    });
    const firstEvidence = evidenceRows[0] || null;
    enriched.push({
      ...row,
      evidence_quote: firstEvidence?.quote || "",
      evidence_count: evidenceRows.length,
      source_answer_id: firstEvidence?.answer_id || null,
    });
  }
  return enriched;
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
    questions.push(...await getOrderedQuestions(store, openid, session.id, project.id));
  }

  const questionIds = new Set(questions.map((question) => question.id));
  const answers = [];
  for (const answer of await store.findMany(COLLECTIONS.answers, { openid })) {
    if (!questionIds.has(answer.question_id) || (answer.project_id && answer.project_id !== project.id)) {
      continue;
    }
    const question = questions.find((item) => item.id === answer.question_id);
    answers.push(await updateProjectScopedRow(store, COLLECTIONS.answers, answer, project.id, {
      session_id: answer.session_id || question?.session_id,
    }));
  }
  const answerIds = new Set(answers.map((answer) => answer.id));
  const drafts = [];
  for (const draft of await store.findMany(COLLECTIONS.drafts, { openid })) {
    if (!answerIds.has(draft.answer_id) || (draft.project_id && draft.project_id !== project.id)) {
      continue;
    }
    const answer = answers.find((item) => item.id === draft.answer_id);
    drafts.push(await updateProjectScopedRow(store, COLLECTIONS.drafts, draft, project.id, {
      session_id: draft.session_id || answer?.session_id,
      question_id: draft.question_id || answer?.question_id,
    }));
  }
  const audioFileIds = new Set(answers.map((answer) => answer.audio_file_id).filter(Boolean));
  const audioFiles = [];
  for (const audioFile of await store.findMany(COLLECTIONS.audioFiles, { openid })) {
    if (!audioFileIds.has(audioFile.id) || (audioFile.project_id && audioFile.project_id !== project.id)) {
      continue;
    }
    audioFiles.push(await updateProjectScopedRow(store, COLLECTIONS.audioFiles, audioFile, project.id));
  }

  return { project, sessions, questions, answers, drafts, audioFiles };
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
        audio_file_id: answer.audio_file_id || null,
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
  const audioFile = rows.audioFiles.find((item) => item.id === answer?.audio_file_id) || null;
  if (!question || !answer) {
    throw new ApiError(404, "故事不存在");
  }

  return {
    story,
    question,
    answer,
    audio_file: audioFile ? {
      id: audioFile.id,
      file_id: audioFile.file_id,
      mime_type: audioFile.mime_type,
      size_bytes: audioFile.size_bytes,
      duration_seconds: audioFile.duration_seconds ?? null,
      transcript_text: audioFile.transcript_text || "",
      transcribed_at: audioFile.transcribed_at || null,
      created_at: audioFile.created_at,
    } : null,
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
  const session = await loadSession(store, openid, sessionId);
  const questions = await getOrderedQuestions(store, openid, session.id, session.project_id);
  const turns = [];
  for (const question of questions) {
    const answer = await store.findOne(COLLECTIONS.answers, {
      openid,
      question_id: question.id,
    });
    if (answer?.project_id && answer.project_id !== session.project_id) {
      continue;
    }
    const text = cleanText(answer?.transcript_edited || answer?.transcript_raw);
    if (!answer || !text) {
      continue;
    }
    const scopedAnswer = await updateProjectScopedRow(store, COLLECTIONS.answers, answer, session.project_id, {
      session_id: answer.session_id || session.id,
    });
    turns.push({
      session_id: sessionId,
      question_id: question.id,
      answer_id: scopedAnswer.id,
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
  const existingRows = await store.findMany(COLLECTIONS.sessionSummaries, {
    openid,
    session_id: sessionId,
  });
  const existing = existingRows.find((row) => !row.project_id || row.project_id === projectId) || null;
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
  const project = await getOrCreateProject(store, openid);
  await loadSession(store, openid, sessionId, project.id);
  const summaries = await store.findMany(COLLECTIONS.sessionSummaries, {
    openid,
    session_id: sessionId,
  });
  const summary = summaries.find((row) => !row.project_id || row.project_id === project.id) || null;
  return summary
    ? updateProjectScopedRow(store, COLLECTIONS.sessionSummaries, summary, project.id)
    : null;
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
  const scopedVersions = [];
  for (const row of versions) {
    if (row.project_id && row.project_id !== projectId) {
      continue;
    }
    scopedVersions.push(await updateProjectScopedRow(store, COLLECTIONS.outputVersions, row, projectId));
  }
  const version = scopedVersions.length
    ? Math.max(...scopedVersions.map((item) => Number(item.version) || 0)) + 1
    : 1;
  await store.insert(COLLECTIONS.outputVersions, {
    openid,
    project_id: projectId,
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
  const project = await getOrCreateProject(store, openid);
  const session = await loadSession(store, openid, sessionId, project.id);
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
  await calculateCoverageSnapshots(store, openid);

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
  const topics = await initializeDefaultCoverageTopics(store, openid, project.id);
  const snapshots = await latestCoverageSnapshots(store, openid, project.id);
  const snapshotByTopicId = new Map(snapshots.map((snapshot) => [snapshot.topic_id, snapshot]));
  return topics.map((topic) => {
    const snapshot = snapshotByTopicId.get(topic.id);
    const keywords = parseJsonArray(topic.keywords_json);
    if (!snapshot) {
      return {
        topic_id: topic.id,
        key: topic.key,
        name: topic.name,
        life_stage: topic.life_stage,
        description: topic.description,
        sort_order: topic.sort_order,
        answered_count: 0,
        memory_count: 0,
        open_question_count: 0,
        coverage_score: 0,
        status: MemoirCoverageStatus.RED,
        signals_json: {
          topic_key: topic.key,
          topic_name: topic.name,
          keywords,
          has_open_questions: false,
        },
        calculated_at: "",
      };
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
  });
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

async function selectCandidateAsQuestion(store, openid, candidateId, options = {}) {
  const project = await getOrCreateProject(store, openid);
  const candidate = await store.findOne(COLLECTIONS.questionCandidates, {
    openid,
    project_id: project.id,
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
  await loadSession(store, openid, candidate.session_id, project.id);
  const existingQuestion = (await getOrderedQuestions(store, openid, candidate.session_id, project.id))
    .find((question) => (
      question.prompt === candidate.question
      && question.status === MemoirQuestionStatus.PENDING
    ));
  let question = existingQuestion;
  if (!question) {
    const questions = await getOrderedQuestions(store, openid, candidate.session_id, project.id);
    const sortOrder = options.preferNow
      ? -1
      : questions.reduce((max, item) => Math.max(max, Number(item.sort_order) || 0), 0) + 1;
    question = await store.insert(COLLECTIONS.questions, {
      openid,
      project_id: project.id,
      session_id: candidate.session_id,
      prompt: candidate.question,
      source: "ai_generated",
      sort_order: sortOrder,
      status: MemoirQuestionStatus.PENDING,
      created_at: nowIso(),
    });
  } else if (options.preferNow && Number(question.sort_order) >= 0) {
    question = await store.updateById(COLLECTIONS.questions, question.id, {
      sort_order: -1,
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
  const project = await getOrCreateProject(store, openid);
  await loadSession(store, openid, sessionId, project.id);
  const existing = (await getOrderedQuestions(store, openid, sessionId, project.id))
    .find((question) => (
      question.source === "ai_generated"
      && question.status === MemoirQuestionStatus.PENDING
    ));
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
  const project = await getOrCreateProject(store, openid);
  const session = await loadSession(store, openid, sessionId, project.id);
  const questions = await getOrderedQuestions(store, openid, sessionId, project.id);
  const currentPendingQuestion = questions.find((question) => (
    question.id === session.current_question_id
    && question.status === MemoirQuestionStatus.PENDING
  ));
  const plannedQuestion = questions.find((question) => (
    question.source === "ai_generated"
    && question.status === MemoirQuestionStatus.PENDING
  ));
  const templateQuestion = questions.find((question) => (
    question.source === "template"
    && question.status === MemoirQuestionStatus.PENDING
  ));
  const keepCurrentQuestion = Boolean(
    currentPendingQuestion
    && (!templateQuestion || Number(currentPendingQuestion.sort_order) <= Number(templateQuestion.sort_order)),
  );
  const nextQuestion = keepCurrentQuestion
    ? currentPendingQuestion
    : templateQuestion
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
  const project = await getOrCreateProject(store, openid);
  const session = await loadSession(store, openid, sessionId, project.id);
  const advancedSession = await advanceSession(store, openid, session.id);
  const questions = await getOrderedQuestions(store, openid, session.id, project.id);
  const currentQuestion = questions.find((question) => question.id === advancedSession.current_question_id) || null;
  const completedStatuses = new Set([
    MemoirQuestionStatus.ANSWERED,
    MemoirQuestionStatus.SKIPPED,
  ]);
  const answers = await store.findMany(COLLECTIONS.answers, {
    openid,
  });
  const sessionAnswers = [];
  for (const answer of answers) {
    if (!questions.some((question) => question.id === answer.question_id)) {
      continue;
    }
    if (answer.project_id && answer.project_id !== project.id) {
      continue;
    }
    sessionAnswers.push(await updateProjectScopedRow(store, COLLECTIONS.answers, answer, project.id, {
      session_id: answer.session_id || session.id,
    }));
  }
  const latestDraft = await getLatestDraft(store, openid, sessionAnswers, project.id);

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
  if (state.current_question) {
    return state.current_question;
  }
  const plannedQuestion = await getOrCreatePlannedQuestion(store, openid, state.session.id);
  return plannedQuestion;
}

async function skipQuestion(store, openid, questionId) {
  const project = await getOrCreateProject(store, openid);
  const question = await loadQuestion(store, openid, questionId, project.id);
  if (question.status === MemoirQuestionStatus.ANSWERED) {
    throw new ApiError(409, "已回答的问题不能跳过");
  }

  await store.updateById(COLLECTIONS.questions, question.id, {
    status: MemoirQuestionStatus.SKIPPED,
  });
  return getSessionState(store, openid, question.session_id);
}

async function answerAudioPatch(store, openid, projectId, data) {
  if (!data.audio_file_id) {
    return {};
  }

  const audioFile = await loadAudioFile(store, openid, data.audio_file_id, projectId);
  return {
    audio_file_id: audioFile.id,
    duration_seconds: cleanPositiveNumber(data.duration_seconds, audioFile.duration_seconds),
  };
}

async function createTextAnswer(store, openid, data) {
  const project = await getOrCreateProject(store, openid);
  const question = await loadQuestion(store, openid, data.question_id, project.id);
  if (question.status === MemoirQuestionStatus.SKIPPED) {
    throw new ApiError(409, "已跳过的问题不能回答");
  }

  const transcript = cleanAnswerText(data.text);
  const audioPatch = await answerAudioPatch(store, openid, project.id, data);
  const now = nowIso();
  const existing = await store.findOne(COLLECTIONS.answers, {
    openid,
    question_id: question.id,
  });
  if (existing?.project_id && existing.project_id !== project.id) {
    throw new ApiError(404, "回答不存在");
  }

  const answer = existing
    ? await store.updateById(COLLECTIONS.answers, existing.id, {
      project_id: project.id,
      session_id: question.session_id,
      transcript_raw: existing.transcript_raw || transcript,
      transcript_edited: transcript,
      status: MemoirAnswerStatus.ANSWERED,
      updated_at: now,
      ...audioPatch,
    })
    : await store.insert(COLLECTIONS.answers, {
      openid,
      project_id: project.id,
      session_id: question.session_id,
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

function cleanAnswerSegmentStatus(value, fallback = MemoirAnswerSegmentStatus.DRAFT) {
  const status = cleanText(value || fallback);
  if (!Object.values(MemoirAnswerSegmentStatus).includes(status)) {
    throw new ApiError(400, "回答分段状态不支持");
  }
  return status;
}

function answerSegmentText(segment) {
  return cleanText(segment.transcript_edited || segment.transcript_raw);
}

function mergeAnswerSegments(segments) {
  return segments
    .map(answerSegmentText)
    .filter(Boolean)
    .join("\n\n");
}

function answerSegmentsDuration(segments) {
  const durations = segments
    .map((segment) => Number(segment.duration_seconds))
    .filter((duration) => Number.isFinite(duration) && duration > 0);
  if (!durations.length) {
    return null;
  }
  return durations.reduce((total, duration) => total + duration, 0);
}

function answerSessionResponse(answerSession, segments = []) {
  const mergedText = mergeAnswerSegments(segments);
  return {
    ...answerSession,
    segment_count: segments.length,
    duration_seconds: answerSession.duration_seconds ?? answerSegmentsDuration(segments),
    transcript_raw: answerSession.transcript_raw || mergedText,
    transcript_edited: answerSession.transcript_edited || mergedText,
    segments,
  };
}

async function listAnswerSegments(store, openid, answerSessionId, projectId) {
  const rows = await store.findMany(COLLECTIONS.answerSegments, {
    openid,
    answer_session_id: answerSessionId,
  }, {
    orderBy: [{ field: "sort_order", direction: "asc" }, { field: "created_at", direction: "asc" }],
  });
  return rows.filter((segment) => !projectId || segment.project_id === projectId);
}

async function getAnswerSessionDetail(store, openid, answerSessionId) {
  const project = await getOrCreateProject(store, openid);
  const answerSession = await loadAnswerSession(store, openid, answerSessionId, project.id);
  const segments = await listAnswerSegments(store, openid, answerSession.id, project.id);
  return answerSessionResponse(answerSession, segments);
}

async function getDraftAnswerSessionForQuestion(store, openid, projectId, questionId) {
  if (!questionId) {
    return null;
  }
  const rows = await store.findMany(COLLECTIONS.answerSessions, {
    openid,
    project_id: projectId,
    question_id: questionId,
    status: MemoirAnswerSessionStatus.DRAFT,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });
  const answerSession = rows[0] || null;
  if (!answerSession) {
    return null;
  }
  const segments = await listAnswerSegments(store, openid, answerSession.id, projectId);
  return answerSessionResponse(answerSession, segments);
}

async function updateAnswerSessionRollup(store, answerSession, segments = null) {
  const sessionSegments = segments || await listAnswerSegments(
    store,
    answerSession.openid,
    answerSession.id,
    answerSession.project_id,
  );
  const mergedText = mergeAnswerSegments(sessionSegments);
  return store.updateById(COLLECTIONS.answerSessions, answerSession.id, {
    segment_count: sessionSegments.length,
    duration_seconds: answerSegmentsDuration(sessionSegments),
    transcript_raw: mergedText,
    transcript_edited: mergedText,
    updated_at: nowIso(),
  });
}

function ensureEditableAnswerSession(answerSession) {
  if (answerSession.status !== MemoirAnswerSessionStatus.DRAFT) {
    throw new ApiError(409, "已提交的回答会话不能修改");
  }
}

async function createAnswerSession(store, openid, questionId) {
  const project = await getOrCreateProject(store, openid);
  const question = await loadQuestion(store, openid, questionId, project.id);
  if (question.status === MemoirQuestionStatus.SKIPPED) {
    throw new ApiError(409, "已跳过的问题不能回答");
  }

  const existingRows = await store.findMany(COLLECTIONS.answerSessions, {
    openid,
    question_id: question.id,
    status: MemoirAnswerSessionStatus.DRAFT,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });
  const existing = existingRows.find((row) => row.project_id === project.id) || null;
  if (existing) {
    return getAnswerSessionDetail(store, openid, existing.id);
  }

  const now = nowIso();
  const answerSession = await store.insert(COLLECTIONS.answerSessions, {
    openid,
    project_id: project.id,
    session_id: question.session_id,
    question_id: question.id,
    answer_id: null,
    status: MemoirAnswerSessionStatus.DRAFT,
    segment_count: 0,
    duration_seconds: null,
    transcript_raw: "",
    transcript_edited: "",
    submitted_at: null,
    created_at: now,
    updated_at: now,
  });
  return answerSessionResponse(answerSession, []);
}

function nextAnswerSegmentSortOrder(segments) {
  if (!segments.length) {
    return 0;
  }
  return Math.max(...segments.map((segment) => cleanNonNegativeInteger(segment.sort_order))) + 1;
}

async function answerSegmentAudioPatch(store, openid, projectId, data) {
  if (!data.audio_file_id) {
    return {};
  }
  const audioFile = await loadAudioFile(store, openid, data.audio_file_id, projectId);
  return {
    audio_file_id: audioFile.id,
    duration_seconds: cleanPositiveNumber(data.duration_seconds, audioFile.duration_seconds),
  };
}

function cleanSegmentTextPatch(data) {
  const transcriptRaw = data.transcript_raw === undefined ? undefined : cleanText(data.transcript_raw);
  const transcriptEdited = data.transcript_edited === undefined ? undefined : cleanText(data.transcript_edited);
  return {
    transcript_raw: transcriptRaw,
    transcript_edited: transcriptEdited,
  };
}

function validateSegmentTextStatus(status, transcriptRaw, transcriptEdited) {
  const text = cleanText(transcriptEdited || transcriptRaw);
  if (status === MemoirAnswerSegmentStatus.TRANSCRIBED && !text) {
    throw new ApiError(400, "已转写分段不能为空");
  }
}

async function appendAnswerSegment(store, openid, answerSessionId, data) {
  const project = await getOrCreateProject(store, openid);
  const answerSession = await loadAnswerSession(store, openid, answerSessionId, project.id);
  ensureEditableAnswerSession(answerSession);
  const existingSegments = await listAnswerSegments(store, openid, answerSession.id, project.id);
  const audioPatch = await answerSegmentAudioPatch(store, openid, project.id, data);
  const textPatch = cleanSegmentTextPatch(data);
  const transcriptRaw = textPatch.transcript_raw || "";
  const transcriptEdited = textPatch.transcript_edited || transcriptRaw;
  const defaultStatus = cleanText(transcriptEdited || transcriptRaw)
    ? MemoirAnswerSegmentStatus.TRANSCRIBED
    : audioPatch.audio_file_id
      ? MemoirAnswerSegmentStatus.UPLOADED
      : MemoirAnswerSegmentStatus.DRAFT;
  const status = cleanAnswerSegmentStatus(data.status, defaultStatus);
  validateSegmentTextStatus(status, transcriptRaw, transcriptEdited);

  const now = nowIso();
  const segment = await store.insert(COLLECTIONS.answerSegments, {
    openid,
    project_id: project.id,
    session_id: answerSession.session_id,
    question_id: answerSession.question_id,
    answer_session_id: answerSession.id,
    audio_file_id: audioPatch.audio_file_id || null,
    sort_order: data.sort_order === undefined
      ? nextAnswerSegmentSortOrder(existingSegments)
      : cleanNonNegativeInteger(data.sort_order),
    status,
    duration_seconds: audioPatch.duration_seconds ?? cleanPositiveNumber(data.duration_seconds),
    transcript_raw: transcriptRaw,
    transcript_edited: transcriptEdited,
    error_message: data.error_message ? cleanText(data.error_message) : "",
    created_at: now,
    updated_at: now,
  });
  await updateAnswerSessionRollup(store, answerSession, [...existingSegments, segment]);
  return segment;
}

async function updateAnswerSegment(store, openid, answerSegmentId, data) {
  const project = await getOrCreateProject(store, openid);
  const segment = await loadAnswerSegment(store, openid, answerSegmentId, project.id);
  const answerSession = await loadAnswerSession(store, openid, segment.answer_session_id, project.id);
  ensureEditableAnswerSession(answerSession);

  const patch = {
    updated_at: nowIso(),
  };
  if (data.sort_order !== undefined) {
    patch.sort_order = cleanNonNegativeInteger(data.sort_order);
  }
  if (data.duration_seconds !== undefined) {
    patch.duration_seconds = cleanPositiveNumber(data.duration_seconds);
  }
  if (data.error_message !== undefined) {
    patch.error_message = cleanText(data.error_message);
  }
  if (data.audio_file_id !== undefined) {
    const audioPatch = await answerSegmentAudioPatch(store, openid, project.id, data);
    patch.audio_file_id = audioPatch.audio_file_id || null;
    if (audioPatch.duration_seconds !== undefined) {
      patch.duration_seconds = audioPatch.duration_seconds;
    }
  }

  const textPatch = cleanSegmentTextPatch(data);
  if (textPatch.transcript_raw !== undefined) {
    patch.transcript_raw = textPatch.transcript_raw;
  }
  if (textPatch.transcript_edited !== undefined) {
    patch.transcript_edited = textPatch.transcript_edited;
  }
  if (data.status !== undefined) {
    patch.status = cleanAnswerSegmentStatus(data.status);
  }

  const nextStatus = patch.status || segment.status;
  const nextTranscriptRaw = patch.transcript_raw === undefined ? segment.transcript_raw : patch.transcript_raw;
  const nextTranscriptEdited = patch.transcript_edited === undefined ? segment.transcript_edited : patch.transcript_edited;
  validateSegmentTextStatus(nextStatus, nextTranscriptRaw, nextTranscriptEdited);

  const updated = await store.updateById(COLLECTIONS.answerSegments, segment.id, patch);
  const segments = await listAnswerSegments(store, openid, answerSession.id, project.id);
  await updateAnswerSessionRollup(store, answerSession, segments);
  return updated;
}

function asrJobResponse(job, segment = null) {
  const usage = parseJsonObject(job.transcript_usage_json);
  return {
    ...job,
    usage: Object.keys(usage).length ? usage : null,
    segment: segment || undefined,
  };
}

function asrErrorMessage(error) {
  return error instanceof Error ? error.message : "转写失败";
}

async function createSegmentTranscriptionJob(store, openid, answerSegmentId) {
  const project = await getOrCreateProject(store, openid);
  const segment = await loadAnswerSegment(store, openid, answerSegmentId, project.id);
  const answerSession = await loadAnswerSession(store, openid, segment.answer_session_id, project.id);
  ensureEditableAnswerSession(answerSession);
  if (!segment.audio_file_id) {
    throw new ApiError(400, "回答分段没有可转写的音频");
  }

  const jobs = await store.findMany(COLLECTIONS.asrJobs, {
    openid,
    answer_segment_id: segment.id,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });
  const reusableJob = jobs.find((job) => (
    job.project_id === project.id
    && job.status !== MemoirAsrJobStatus.FAILED
  ));
  if (reusableJob) {
    return asrJobResponse(reusableJob, segment);
  }

  const now = nowIso();
  const job = await store.insert(COLLECTIONS.asrJobs, {
    openid,
    project_id: project.id,
    answer_session_id: answerSession.id,
    answer_segment_id: segment.id,
    audio_file_id: segment.audio_file_id,
    status: MemoirAsrJobStatus.QUEUED,
    transcript_text: "",
    transcript_usage_json: "",
    error_message: "",
    created_at: now,
    updated_at: now,
    started_at: null,
    completed_at: null,
  });
  return asrJobResponse(job, segment);
}

async function getAsrJob(store, openid, asrJobId) {
  const project = await getOrCreateProject(store, openid);
  const job = await loadAsrJob(store, openid, asrJobId, project.id);
  const segment = await loadAnswerSegment(store, openid, job.answer_segment_id, project.id);
  return asrJobResponse(job, segment);
}

async function processAsrJob(store, openid, asrJobId, dependencies) {
  const project = await getOrCreateProject(store, openid);
  const job = await loadAsrJob(store, openid, asrJobId, project.id);
  const segment = await loadAnswerSegment(store, openid, job.answer_segment_id, project.id);
  if (job.status === MemoirAsrJobStatus.COMPLETED || job.status === MemoirAsrJobStatus.FAILED) {
    return asrJobResponse(job, segment);
  }
  const answerSession = await loadAnswerSession(store, openid, segment.answer_session_id, project.id);
  ensureEditableAnswerSession(answerSession);
  if (!dependencies.storage?.downloadFile) {
    throw new ApiError(500, "云存储未配置");
  }
  if (!dependencies.asr?.transcribe) {
    throw new ApiError(500, "语音转写服务未配置");
  }

  const audioFile = await loadAudioFile(store, openid, job.audio_file_id || segment.audio_file_id, project.id);
  const startedAt = nowIso();
  await store.updateById(COLLECTIONS.asrJobs, job.id, {
    status: MemoirAsrJobStatus.RUNNING,
    error_message: "",
    started_at: job.started_at || startedAt,
    updated_at: startedAt,
  });
  await store.updateById(COLLECTIONS.answerSegments, segment.id, {
    status: MemoirAnswerSegmentStatus.TRANSCRIBING,
    error_message: "",
    updated_at: startedAt,
  });

  try {
    const audioBytes = await dependencies.storage.downloadFile(audioFile.file_id);
    const result = await dependencies.asr.transcribe(audioBytes, audioFile);
    const text = cleanAnswerText(result.text);
    const completedAt = nowIso();
    await store.updateById(COLLECTIONS.audioFiles, audioFile.id, {
      transcript_text: text,
      transcript_usage_json: serializeJsonField(result.usage),
      transcribed_at: completedAt,
    });
    const updatedSegment = await store.updateById(COLLECTIONS.answerSegments, segment.id, {
      transcript_raw: text,
      transcript_edited: text,
      status: MemoirAnswerSegmentStatus.TRANSCRIBED,
      error_message: "",
      updated_at: completedAt,
    });
    const segments = await listAnswerSegments(store, openid, answerSession.id, project.id);
    await updateAnswerSessionRollup(store, answerSession, segments);
    const completedJob = await store.updateById(COLLECTIONS.asrJobs, job.id, {
      status: MemoirAsrJobStatus.COMPLETED,
      transcript_text: text,
      transcript_usage_json: serializeJsonField(result.usage),
      error_message: "",
      completed_at: completedAt,
      updated_at: completedAt,
    });
    return asrJobResponse(completedJob, updatedSegment);
  } catch (error) {
    const failedAt = nowIso();
    const message = asrErrorMessage(error);
    const failedSegment = await store.updateById(COLLECTIONS.answerSegments, segment.id, {
      status: MemoirAnswerSegmentStatus.FAILED,
      error_message: message,
      updated_at: failedAt,
    });
    const failedJob = await store.updateById(COLLECTIONS.asrJobs, job.id, {
      status: MemoirAsrJobStatus.FAILED,
      error_message: message,
      completed_at: failedAt,
      updated_at: failedAt,
    });
    return asrJobResponse(failedJob, failedSegment);
  }
}

async function submitAnswerSession(store, openid, answerSessionId) {
  const project = await getOrCreateProject(store, openid);
  const answerSession = await loadAnswerSession(store, openid, answerSessionId, project.id);
  if (answerSession.status === MemoirAnswerSessionStatus.SUBMITTED) {
    return getAnswerSessionDetail(store, openid, answerSession.id);
  }

  const question = await loadQuestion(store, openid, answerSession.question_id, project.id);
  if (question.status === MemoirQuestionStatus.SKIPPED) {
    throw new ApiError(409, "已跳过的问题不能回答");
  }

  const segments = await listAnswerSegments(store, openid, answerSession.id, project.id);
  const mergedText = mergeAnswerSegments(segments);
  if (!mergedText) {
    throw new ApiError(400, "回答会话没有可提交的文字");
  }

  const audioFileIds = [...new Set(segments.map((segment) => segment.audio_file_id).filter(Boolean))];
  const primaryAudioFileId = audioFileIds.length === 1 ? audioFileIds[0] : null;
  const durationSeconds = answerSegmentsDuration(segments);
  const answer = await createTextAnswer(store, openid, {
    question_id: question.id,
    text: mergedText,
    audio_file_id: primaryAudioFileId || undefined,
    duration_seconds: durationSeconds,
  });
  const now = nowIso();
  const updatedAnswer = await store.updateById(COLLECTIONS.answers, answer.id, {
    project_id: project.id,
    session_id: question.session_id,
    question_id: question.id,
    answer_session_id: answerSession.id,
    audio_file_id: primaryAudioFileId,
    transcript_raw: mergedText,
    transcript_edited: mergedText,
    status: MemoirAnswerStatus.ANSWERED,
    duration_seconds: durationSeconds,
    updated_at: now,
  });
  const submittedSession = await store.updateById(COLLECTIONS.answerSessions, answerSession.id, {
    answer_id: updatedAnswer.id,
    status: MemoirAnswerSessionStatus.SUBMITTED,
    segment_count: segments.length,
    duration_seconds: durationSeconds,
    transcript_raw: mergedText,
    transcript_edited: mergedText,
    submitted_at: now,
    updated_at: now,
  });

  return answerSessionResponse(submittedSession, segments);
}

async function updateAnswerText(store, openid, answerId, data) {
  const project = await getOrCreateProject(store, openid);
  const answer = await loadAnswer(store, openid, answerId, project.id);
  const text = cleanAnswerText(data.text);
  const updated = await store.updateById(COLLECTIONS.answers, answer.id, {
    project_id: project.id,
    transcript_edited: text,
    status: MemoirAnswerStatus.ANSWERED,
    updated_at: nowIso(),
  });

  const question = await loadQuestion(store, openid, updated.question_id, project.id);
  await store.updateById(COLLECTIONS.questions, question.id, {
    status: MemoirQuestionStatus.ANSWERED,
  });
  await advanceSession(store, openid, question.session_id);
  return updated;
}

async function deleteAnswer(store, openid, answerId) {
  const project = await getOrCreateProject(store, openid);
  const answer = await loadAnswer(store, openid, answerId, project.id);
  const question = await loadQuestion(store, openid, answer.question_id, project.id);
  const drafts = (await store.findMany(COLLECTIONS.drafts, {
    openid,
    answer_id: answer.id,
  })).filter((draft) => !draft.project_id || draft.project_id === project.id);

  for (const draft of drafts) {
    await store.deleteById(COLLECTIONS.drafts, draft.id);
  }
  await store.deleteById(COLLECTIONS.answers, answer.id);
  await store.updateById(COLLECTIONS.questions, question.id, {
    status: MemoirQuestionStatus.PENDING,
  });
  await advanceSession(store, openid, question.session_id);

  return {
    id: answer.id,
    question_id: question.id,
    deleted: true,
  };
}

async function registerAudioFile(store, openid, data) {
  const project = await getOrCreateProject(store, openid);
  const now = nowIso();
  const cloudPath = cleanCloudAudioPath(openid, data.cloud_path);
  const extension = audioExtension(cloudPath);
  const audioFile = await store.insert(COLLECTIONS.audioFiles, {
    openid,
    project_id: project.id,
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

async function loadAudioFile(store, openid, audioFileId, projectId = null) {
  const audioFile = await store.findOne(COLLECTIONS.audioFiles, {
    openid,
    _id: audioFileId,
  });
  if (!audioFile) {
    throw new ApiError(404, "音频文件不存在");
  }
  if (!projectId) {
    return audioFile;
  }
  if (audioFile.project_id && audioFile.project_id !== projectId) {
    throw new ApiError(404, "音频文件不存在");
  }
  return updateProjectScopedRow(store, COLLECTIONS.audioFiles, audioFile, projectId);
}

async function transcribeAudioFile(store, openid, audioFileId, dependencies) {
  if (!dependencies.storage?.downloadFile) {
    throw new ApiError(500, "云存储未配置");
  }
  if (!dependencies.asr?.transcribe) {
    throw new ApiError(500, "语音转写服务未配置");
  }

  const project = await getOrCreateProject(store, openid);
  const audioFile = await loadAudioFile(store, openid, audioFileId, project.id);
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

function cleanTtsSourceType(value) {
  const sourceType = cleanText(value).toLowerCase();
  if ([
    "question",
    "open_question",
    "answer",
    "draft",
    "output",
    "output_version",
    "session_summary",
  ].includes(sourceType)) {
    return sourceType;
  }
  throw new ApiError(400, "朗读来源类型不支持");
}

function cleanTtsFormat(value) {
  const format = cleanText(value || DEFAULT_TTS_FORMAT).toLowerCase();
  if (!TTS_MIME_BY_FORMAT[format]) {
    throw new ApiError(400, "朗读音频格式暂不支持");
  }
  return format;
}

function cleanTtsSpeed(value) {
  if (value === undefined || value === null || value === "") {
    return DEFAULT_TTS_SPEED;
  }
  const speed = Number(value);
  if (!Number.isFinite(speed)) {
    return DEFAULT_TTS_SPEED;
  }
  return Number(Math.max(0.5, Math.min(2, speed)).toFixed(2));
}

function cleanTtsOptions(data = {}) {
  return {
    model: cleanText(data.model) || DEFAULT_TTS_MODEL,
    voice: cleanText(data.voice) || DEFAULT_TTS_VOICE,
    format: cleanTtsFormat(data.format),
    synthesis_speed: cleanTtsSpeed(data.synthesis_speed),
  };
}

function requireReadableText(text) {
  const value = cleanText(text).replace(/\s+/g, " ");
  if (!value) {
    throw new ApiError(400, "朗读内容不能为空");
  }
  return value;
}

function ttsSourceVersion(row, fallback = "") {
  return String(row.version || row.updated_at || row.created_at || fallback || "");
}

async function resolveTtsSource(store, openid, projectId, sourceTypeInput, sourceId) {
  const sourceType = cleanTtsSourceType(sourceTypeInput);
  if (sourceType === "question") {
    const question = await loadQuestion(store, openid, sourceId, projectId);
    return {
      source_type: sourceType,
      source_id: question.id,
      source_version: ttsSourceVersion(question),
      title: "问题",
      text: requireReadableText(question.prompt),
    };
  }

  if (sourceType === "open_question") {
    const question = await loadMemory(store, openid, projectId, MemoirMemoryType.OPEN_QUESTION, sourceId);
    return {
      source_type: sourceType,
      source_id: question.id,
      source_version: ttsSourceVersion(question),
      title: "待确认问题",
      text: requireReadableText(question.question),
    };
  }

  if (sourceType === "answer") {
    const answer = await loadAnswer(store, openid, sourceId, projectId);
    return {
      source_type: sourceType,
      source_id: answer.id,
      source_version: ttsSourceVersion(answer),
      title: "回答",
      text: requireReadableText(answer.transcript_edited || answer.transcript_raw),
    };
  }

  if (sourceType === "draft") {
    const draft = await loadDraft(store, openid, sourceId, projectId);
    return {
      source_type: sourceType,
      source_id: draft.id,
      source_version: ttsSourceVersion(draft),
      title: "整理稿",
      text: requireReadableText(draft.content_edited || draft.content_raw),
    };
  }

  if (sourceType === "session_summary") {
    const summary = await store.findOne(COLLECTIONS.sessionSummaries, {
      openid,
      _id: sourceId,
    });
    if (!summary || (summary.project_id && summary.project_id !== projectId)) {
      throw new ApiError(404, "访谈摘要不存在");
    }
    const scopedSummary = await updateProjectScopedRow(store, COLLECTIONS.sessionSummaries, summary, projectId);
    return {
      source_type: sourceType,
      source_id: scopedSummary.id,
      source_version: ttsSourceVersion(scopedSummary),
      title: "访谈摘要",
      text: requireReadableText(scopedSummary.summary),
    };
  }

  if (sourceType === "output") {
    const output = await store.findOne(COLLECTIONS.outputs, {
      openid,
      project_id: projectId,
      _id: sourceId,
    });
    if (!output) {
      throw new ApiError(404, "写作产物不存在");
    }
    const versions = await store.findMany(COLLECTIONS.outputVersions, {
      openid,
      output_id: output.id,
    }, {
      orderBy: [{ field: "version", direction: "desc" }],
    });
    const latestVersion = versions.find((version) => !version.project_id || version.project_id === projectId);
    if (!latestVersion) {
      throw new ApiError(400, "写作产物没有可朗读内容");
    }
    const scopedVersion = await updateProjectScopedRow(store, COLLECTIONS.outputVersions, latestVersion, projectId);
    return {
      source_type: sourceType,
      source_id: output.id,
      source_version: String(scopedVersion.version),
      title: output.title || "写作产物",
      text: requireReadableText(scopedVersion.content),
    };
  }

  const version = await store.findOne(COLLECTIONS.outputVersions, {
    openid,
    _id: sourceId,
  });
  if (!version || (version.project_id && version.project_id !== projectId)) {
    throw new ApiError(404, "写作产物版本不存在");
  }
  const output = await store.findOne(COLLECTIONS.outputs, {
    openid,
    _id: version.output_id,
  });
  if (!output || (output.project_id && output.project_id !== projectId)) {
    throw new ApiError(404, "写作产物不存在");
  }
  const scopedVersion = await updateProjectScopedRow(store, COLLECTIONS.outputVersions, version, projectId);
  return {
    source_type: sourceType,
    source_id: scopedVersion.id,
    source_version: String(scopedVersion.version),
    title: output.title || "写作产物",
    text: requireReadableText(scopedVersion.content),
  };
}

function ttsAssetResponse(asset) {
  return {
    id: asset.id,
    project_id: asset.project_id,
    source_type: asset.source_type,
    source_id: asset.source_id,
    source_version: asset.source_version,
    text_hash: asset.text_hash,
    text_preview: asset.text_preview,
    text_length: asset.text_length,
    file_id: asset.file_id,
    cloud_path: asset.cloud_path,
    mime_type: asset.mime_type,
    size_bytes: asset.size_bytes,
    model: asset.model,
    voice: asset.voice,
    format: asset.format,
    synthesis_speed: asset.synthesis_speed,
    chunk_count: asset.chunk_count,
    created_at: asset.created_at,
    updated_at: asset.updated_at,
    generated_at: asset.generated_at,
  };
}

function ttsJobResponse(job, asset = null) {
  return {
    id: job.id,
    project_id: job.project_id,
    source_type: job.source_type,
    source_id: job.source_id,
    source_version: job.source_version,
    text_hash: job.text_hash,
    text_preview: job.text_preview,
    text_length: job.text_length,
    tts_asset_id: job.tts_asset_id || null,
    status: job.status,
    model: job.model,
    voice: job.voice,
    format: job.format,
    synthesis_speed: job.synthesis_speed,
    chunk_count: job.chunk_count || 0,
    error_message: job.error_message || "",
    created_at: job.created_at,
    updated_at: job.updated_at,
    started_at: job.started_at || null,
    completed_at: job.completed_at || null,
    asset: asset ? ttsAssetResponse(asset) : undefined,
  };
}

async function findTtsAsset(store, openid, projectId, source, options) {
  return store.findOne(COLLECTIONS.ttsAssets, {
    openid,
    project_id: projectId,
    source_type: source.source_type,
    source_id: source.source_id,
    source_version: source.source_version,
    text_hash: textHash(source.text),
    model: options.model,
    voice: options.voice,
    format: options.format,
    synthesis_speed: options.synthesis_speed,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });
}

async function findReusableTtsJob(store, openid, projectId, source, options) {
  const jobs = await store.findMany(COLLECTIONS.ttsJobs, {
    openid,
    project_id: projectId,
    source_type: source.source_type,
    source_id: source.source_id,
    source_version: source.source_version,
    text_hash: textHash(source.text),
    model: options.model,
    voice: options.voice,
    format: options.format,
    synthesis_speed: options.synthesis_speed,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });
  return jobs.find((job) => [
    MemoirTtsJobStatus.QUEUED,
    MemoirTtsJobStatus.RUNNING,
  ].includes(job.status)) || null;
}

async function loadTtsAsset(store, openid, ttsAssetId, projectId = null) {
  const asset = await store.findOne(COLLECTIONS.ttsAssets, {
    openid,
    _id: ttsAssetId,
  });
  if (!asset) {
    throw new ApiError(404, "朗读音频不存在");
  }
  if (projectId && asset.project_id !== projectId) {
    throw new ApiError(404, "朗读音频不存在");
  }
  return asset;
}

async function loadTtsJob(store, openid, ttsJobId, projectId = null) {
  const job = await store.findOne(COLLECTIONS.ttsJobs, {
    openid,
    _id: ttsJobId,
  });
  if (!job) {
    throw new ApiError(404, "朗读任务不存在");
  }
  if (projectId && job.project_id !== projectId) {
    throw new ApiError(404, "朗读任务不存在");
  }
  return job;
}

async function createTtsAsset(store, openid, data) {
  const project = await getOrCreateProject(store, openid);
  const source = await resolveTtsSource(store, openid, project.id, data.source_type, data.source_id);
  const options = cleanTtsOptions(data);
  const existingAsset = await findTtsAsset(store, openid, project.id, source, options);
  if (existingAsset) {
    return {
      asset: ttsAssetResponse(existingAsset),
      job: null,
      reused: true,
    };
  }

  const reusableJob = await findReusableTtsJob(store, openid, project.id, source, options);
  if (reusableJob) {
    return {
      asset: null,
      job: ttsJobResponse(reusableJob),
      reused: true,
    };
  }

  const now = nowIso();
  const job = await store.insert(COLLECTIONS.ttsJobs, {
    openid,
    project_id: project.id,
    source_type: source.source_type,
    source_id: source.source_id,
    source_version: source.source_version,
    text_hash: textHash(source.text),
    text_preview: previewText(source.text),
    text_length: source.text.length,
    tts_asset_id: null,
    status: MemoirTtsJobStatus.QUEUED,
    model: options.model,
    voice: options.voice,
    format: options.format,
    synthesis_speed: options.synthesis_speed,
    chunk_count: 0,
    error_message: "",
    created_at: now,
    updated_at: now,
    started_at: null,
    completed_at: null,
  });

  return {
    asset: null,
    job: ttsJobResponse(job),
    reused: false,
  };
}

async function getTtsAsset(store, openid, ttsAssetId) {
  const project = await getOrCreateProject(store, openid);
  return ttsAssetResponse(await loadTtsAsset(store, openid, ttsAssetId, project.id));
}

async function getTtsJob(store, openid, ttsJobId) {
  const project = await getOrCreateProject(store, openid);
  const job = await loadTtsJob(store, openid, ttsJobId, project.id);
  const asset = job.tts_asset_id
    ? await loadTtsAsset(store, openid, job.tts_asset_id, project.id)
    : null;
  return ttsJobResponse(job, asset);
}

function ttsErrorMessage(error) {
  return error instanceof Error ? error.message : "朗读生成失败";
}

async function processTtsJob(store, openid, ttsJobId, dependencies) {
  const project = await getOrCreateProject(store, openid);
  const job = await loadTtsJob(store, openid, ttsJobId, project.id);
  if (job.status === MemoirTtsJobStatus.COMPLETED || job.status === MemoirTtsJobStatus.FAILED) {
    const asset = job.tts_asset_id
      ? await loadTtsAsset(store, openid, job.tts_asset_id, project.id)
      : null;
    return ttsJobResponse(job, asset);
  }
  if (!dependencies.tts?.synthesize) {
    throw new ApiError(500, "语音朗读服务未配置");
  }
  if (!dependencies.storage?.uploadFile) {
    throw new ApiError(500, "云存储未配置");
  }

  const startedAt = nowIso();
  await store.updateById(COLLECTIONS.ttsJobs, job.id, {
    status: MemoirTtsJobStatus.RUNNING,
    error_message: "",
    started_at: job.started_at || startedAt,
    updated_at: startedAt,
  });

  try {
    const source = await resolveTtsSource(store, openid, project.id, job.source_type, job.source_id);
    if (source.source_version !== job.source_version || textHash(source.text) !== job.text_hash) {
      throw new ApiError(409, "朗读内容已更新，请重新生成");
    }

    const result = await dependencies.tts.synthesize({
      text: source.text,
      model: job.model,
      voice: job.voice,
      format: job.format,
      speed: job.synthesis_speed,
    });
    const audioBytes = result.audioBytes;
    if (!Buffer.isBuffer(audioBytes) || audioBytes.length === 0) {
      throw new ApiError(503, "语音朗读服务返回了空音频");
    }

    const cloudPath = `${CLOUD_TTS_ROOT}/${safePathSegment(openid)}/${job.id}-${job.text_hash.slice(0, 12)}.${job.format}`;
    const fileId = await dependencies.storage.uploadFile(cloudPath, audioBytes, result.mime_type || TTS_MIME_BY_FORMAT[job.format]);
    const completedAt = nowIso();
    const asset = await store.insert(COLLECTIONS.ttsAssets, {
      openid,
      project_id: project.id,
      source_type: source.source_type,
      source_id: source.source_id,
      source_version: source.source_version,
      text_hash: job.text_hash,
      text_preview: job.text_preview,
      text_length: job.text_length,
      file_id: fileId,
      cloud_path: cloudPath,
      mime_type: result.mime_type || TTS_MIME_BY_FORMAT[job.format],
      size_bytes: audioBytes.length,
      model: job.model,
      voice: job.voice,
      format: job.format,
      synthesis_speed: job.synthesis_speed,
      chunk_count: result.chunk_count || 1,
      created_at: completedAt,
      updated_at: completedAt,
      generated_at: completedAt,
    });
    const completedJob = await store.updateById(COLLECTIONS.ttsJobs, job.id, {
      tts_asset_id: asset.id,
      status: MemoirTtsJobStatus.COMPLETED,
      chunk_count: asset.chunk_count,
      error_message: "",
      completed_at: completedAt,
      updated_at: completedAt,
    });
    return ttsJobResponse(completedJob, asset);
  } catch (error) {
    const failedAt = nowIso();
    const failedJob = await store.updateById(COLLECTIONS.ttsJobs, job.id, {
      status: MemoirTtsJobStatus.FAILED,
      error_message: ttsErrorMessage(error),
      completed_at: failedAt,
      updated_at: failedAt,
    });
    return ttsJobResponse(failedJob);
  }
}

async function generateDraft(store, openid, answerId, dependencies) {
  if (!dependencies.drafting?.generate) {
    throw new ApiError(500, "AI 整理服务未配置");
  }

  const project = await getOrCreateProject(store, openid);
  const answer = await loadAnswer(store, openid, answerId, project.id);
  if (answer.status === MemoirAnswerStatus.CONFIRMED) {
    throw new ApiError(409, "已确认的回答不能重新整理");
  }

  const question = await loadQuestion(store, openid, answer.question_id, project.id);
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
  if (existing?.project_id && existing.project_id !== project.id) {
    throw new ApiError(404, "整理稿不存在");
  }

  const draft = existing
    ? await store.updateById(COLLECTIONS.drafts, existing.id, {
      project_id: project.id,
      session_id: answer.session_id || question.session_id,
      question_id: question.id,
      content_raw: draftText,
      content_edited: draftText,
      status: MemoirDraftStatus.DRAFT,
      updated_at: now,
    })
    : await store.insert(COLLECTIONS.drafts, {
      openid,
      project_id: project.id,
      session_id: answer.session_id || question.session_id,
      question_id: question.id,
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
  const project = await getOrCreateProject(store, openid);
  const draft = await loadDraft(store, openid, draftId, project.id);
  if (draft.status === MemoirDraftStatus.ACCEPTED) {
    throw new ApiError(409, "已确认的整理稿不能编辑");
  }

  return store.updateById(COLLECTIONS.drafts, draft.id, {
    content_edited: cleanDraftText(data.content_edited),
    updated_at: nowIso(),
  });
}

async function acceptDraft(store, openid, draftId) {
  const project = await getOrCreateProject(store, openid);
  const draft = await loadDraft(store, openid, draftId, project.id);
  await loadAnswer(store, openid, draft.answer_id, project.id);
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
  const outputs = await store.findMany(COLLECTIONS.outputs, {
    openid,
    project_id: project.id,
  }, {
    orderBy: [{ field: "updated_at", direction: "desc" }],
  });
  const enriched = [];
  for (const output of outputs) {
    const versions = await store.findMany(COLLECTIONS.outputVersions, {
      openid,
      output_id: output.id,
    }, {
      orderBy: [{ field: "version", direction: "desc" }],
    });
    const scopedVersions = [];
    for (const version of versions) {
      if (version.project_id && version.project_id !== project.id) {
        continue;
      }
      scopedVersions.push(await updateProjectScopedRow(store, COLLECTIONS.outputVersions, version, project.id));
    }
    const latestVersion = scopedVersions[0] || null;
    enriched.push({
      ...output,
      content_preview: latestVersion ? previewText(latestVersion.content) : "",
      latest_version: latestVersion ? latestVersion.version : null,
      latest_version_created_at: latestVersion ? latestVersion.created_at : null,
      version_count: scopedVersions.length,
    });
  }
  return enriched;
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
  const scopedVersions = [];
  for (const version of versions) {
    if (version.project_id && version.project_id !== project.id) {
      continue;
    }
    scopedVersions.push(await updateProjectScopedRow(store, COLLECTIONS.outputVersions, version, project.id));
  }
  return {
    ...output,
    versions: scopedVersions,
  };
}

function countRows(rows, predicate) {
  return rows.filter(predicate).length;
}

function averageCoverage(coverage) {
  if (!coverage.length) {
    return 0;
  }
  return Math.round(coverage.reduce((total, item) => total + (Number(item.coverage_score) || 0), 0) / coverage.length);
}

function memoryStats({ people, events, places, themes, openQuestions, coverage, candidates, outputs }) {
  const memories = [...people, ...events, ...places, ...themes];
  return {
    total_memory_count: memories.length,
    draft_memory_count: countRows(memories, (item) => item.status === MemoirMemoryStatus.DRAFT),
    confirmed_memory_count: countRows(memories, (item) => item.status === MemoirMemoryStatus.CONFIRMED),
    rejected_memory_count: countRows(memories, (item) => item.status === MemoirMemoryStatus.REJECTED),
    open_question_count: countRows(openQuestions, (item) => item.status === MemoirOpenQuestionStatus.OPEN),
    asked_open_question_count: countRows(openQuestions, (item) => item.status === MemoirOpenQuestionStatus.ASKED),
    answered_open_question_count: countRows(openQuestions, (item) => item.status === MemoirOpenQuestionStatus.ANSWERED),
    dismissed_open_question_count: countRows(openQuestions, (item) => item.status === MemoirOpenQuestionStatus.DISMISSED),
    question_candidate_count: candidates.length,
    output_count: outputs.length,
    coverage_average: averageCoverage(coverage),
    low_coverage_count: countRows(coverage, (item) => item.status !== MemoirCoverageStatus.GREEN),
  };
}

async function getMemoryDashboard(store, openid) {
  const project = await getOrCreateProject(store, openid);
  const interview = await getOrCreateCurrentSession(store, openid);
  const activeAnswerSession = await getDraftAnswerSessionForQuestion(
    store,
    openid,
    project.id,
    interview.current_question?.id,
  );
  const [
    summary,
    sessionSummary,
    people,
    events,
    places,
    themes,
    openQuestions,
    coverage,
    candidates,
    outputs,
  ] = await Promise.all([
    getSummary(store, openid),
    getSessionSummary(store, openid, interview.session.id),
    listMemoryRows(store, openid, project.id, MemoirMemoryType.PERSON),
    listMemoryRows(store, openid, project.id, MemoirMemoryType.EVENT),
    listMemoryRows(store, openid, project.id, MemoirMemoryType.PLACE),
    listMemoryRows(store, openid, project.id, MemoirMemoryType.THEME),
    listOpenQuestions(store, openid),
    getCoverageMap(store, openid),
    listQuestionCandidates(store, openid),
    listOutputs(store, openid),
  ]);
  return {
    project: createProjectResponse(project),
    interview,
    active_answer_session: activeAnswerSession,
    summary,
    session_summary: sessionSummary,
    people,
    events,
    places,
    themes,
    open_questions: openQuestions,
    coverage,
    candidates,
    outputs,
    memory_stats: memoryStats({
      people,
      events,
      places,
      themes,
      openQuestions,
      coverage,
      candidates,
      outputs,
    }),
  };
}

function createMemoirService(store, context, dependencies = {}) {
  const openid = requireOpenId(context);

  return {
    getMe: async () => getMemoirIdentity(store, openid),
    updateMe: async (data) => {
      const user = await getOrCreateMemoirUser(store, openid, data);
      await renameDefaultProjectForUser(store, openid, user);
      return createUserResponse(user);
    },
    getProject: async () => createProjectResponse(await getOrCreateProject(store, openid)),
    listProjects: async () => {
      const user = await getOrCreateMemoirUser(store, openid);
      const activeProject = await getActiveProject(store, openid, user);
      const projects = await listUserProjects(store, openid, user);
      return {
        active_project_id: activeProject.id,
        active_project: createProjectResponse(activeProject),
        projects: projects.map(createProjectResponse),
      };
    },
    createProject: async (data) => createProjectResponse(await createMemoirProject(store, openid, data)),
    updateProject: async (projectId, data) => createProjectResponse(await updateMemoirProject(store, openid, projectId, data)),
    activateProject: async (projectId) => {
      const project = await activateMemoirProject(store, openid, projectId);
      return {
        active_project_id: project.id,
        active_project: createProjectResponse(project),
      };
    },
    getMemoryDashboard: async () => getMemoryDashboard(store, openid),
    getCurrentSession: async () => getOrCreateCurrentSession(store, openid),
    getNextQuestion: async () => getNextQuestion(store, openid),
    getSummary: async () => getSummary(store, openid),
    listStoryCards: async () => listStoryCards(store, openid),
    getStoryDetail: async (answerId) => getStoryDetail(store, openid, answerId),
    getAnswerSession: async (answerSessionId) => getAnswerSessionDetail(store, openid, answerSessionId),
    getAsrJob: async (asrJobId) => getAsrJob(store, openid, asrJobId),
    getTtsJob: async (ttsJobId) => getTtsJob(store, openid, ttsJobId),
    getTtsAsset: async (ttsAssetId) => getTtsAsset(store, openid, ttsAssetId),
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
    selectCandidateAsQuestion: async (candidateId) => selectCandidateAsQuestion(store, openid, candidateId, {
      preferNow: true,
    }),
    listOutputs: async () => listOutputs(store, openid),
    getOutput: async (outputId) => getOutput(store, openid, outputId),
    skipQuestion: async (questionId) => skipQuestion(store, openid, questionId),
    createAnswerSession: async (questionId) => createAnswerSession(store, openid, questionId),
    appendAnswerSegment: async (answerSessionId, data) => appendAnswerSegment(store, openid, answerSessionId, data),
    updateAnswerSegment: async (answerSegmentId, data) => updateAnswerSegment(store, openid, answerSegmentId, data),
    createSegmentTranscriptionJob: async (answerSegmentId) => createSegmentTranscriptionJob(store, openid, answerSegmentId),
    processAsrJob: async (asrJobId) => processAsrJob(store, openid, asrJobId, dependencies),
    createTtsAsset: async (data) => createTtsAsset(store, openid, data),
    processTtsJob: async (ttsJobId) => processTtsJob(store, openid, ttsJobId, dependencies),
    submitAnswerSession: async (answerSessionId) => submitAnswerSession(store, openid, answerSessionId),
    createTextAnswer: async (data) => createTextAnswer(store, openid, data),
    updateAnswerText: async (answerId, data) => updateAnswerText(store, openid, answerId, data),
    deleteAnswer: async (answerId) => deleteAnswer(store, openid, answerId),
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
  MemoirAnswerSegmentStatus,
  MemoirAnswerSessionStatus,
  MemoirAsrJobStatus,
  MemoirTtsJobStatus,
  MemoirDraftStatus,
  MemoirQuestionStatus,
  MemoirSessionStatus,
};
