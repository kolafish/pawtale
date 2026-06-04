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
  ANSWERED: "answered",
});

function nowIso() {
  return new Date().toISOString();
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

async function createTextAnswer(store, openid, data) {
  const question = await loadQuestion(store, openid, data.question_id);
  if (question.status === MemoirQuestionStatus.SKIPPED) {
    throw new ApiError(409, "已跳过的问题不能回答");
  }

  const transcript = cleanAnswerText(data.text);
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
    })
    : await store.insert(COLLECTIONS.answers, {
      openid,
      question_id: question.id,
      audio_file_id: null,
      transcript_raw: transcript,
      transcript_edited: transcript,
      status: MemoirAnswerStatus.ANSWERED,
      duration_seconds: null,
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

function createMemoirService(store, context) {
  const openid = requireOpenId(context);

  return {
    getProject: async () => createProjectResponse(await getOrCreateProject(store, openid)),
    getCurrentSession: async () => getOrCreateCurrentSession(store, openid),
    getNextQuestion: async () => getNextQuestion(store, openid),
    skipQuestion: async (questionId) => skipQuestion(store, openid, questionId),
    createTextAnswer: async (data) => createTextAnswer(store, openid, data),
    updateAnswerText: async (answerId, data) => updateAnswerText(store, openid, answerId, data),
  };
}

module.exports = {
  createMemoirService,
  MemoirAnswerStatus,
  MemoirQuestionStatus,
  MemoirSessionStatus,
};
