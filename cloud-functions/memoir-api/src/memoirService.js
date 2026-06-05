const { ApiError } = require("./http");
const { COLLECTIONS } = require("./collections");
const {
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

const STORY_PREVIEW_LIMIT = 80;

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

function cleanRequiredText(value, message) {
  const text = String(value || "").trim();
  if (!text) {
    throw new ApiError(400, message);
  }
  return text;
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

async function advanceSession(store, openid, sessionId) {
  const questions = await getOrderedQuestions(store, openid, sessionId);
  const nextQuestion = questions.find((question) => question.status === MemoirQuestionStatus.PENDING) || null;
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
  return state.current_question;
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
  const audioFile = await store.insert(COLLECTIONS.audioFiles, {
    openid,
    file_id: cleanRequiredText(data.file_id, "缺少云存储文件 ID"),
    cloud_path: cleanRequiredText(data.cloud_path, "缺少云存储路径"),
    mime_type: String(data.mime_type || "audio/mpeg"),
    size_bytes: cleanPositiveNumber(data.size_bytes, 0),
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

function createMemoirService(store, context, dependencies = {}) {
  const openid = requireOpenId(context);

  return {
    getProject: async () => createProjectResponse(await getOrCreateProject(store, openid)),
    getCurrentSession: async () => getOrCreateCurrentSession(store, openid),
    getNextQuestion: async () => getNextQuestion(store, openid),
    getSummary: async () => getSummary(store, openid),
    listStoryCards: async () => listStoryCards(store, openid),
    getStoryDetail: async (answerId) => getStoryDetail(store, openid, answerId),
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
