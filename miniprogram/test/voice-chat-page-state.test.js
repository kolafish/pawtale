require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  initialVoiceChatData,
  applyVoiceChatDataPatch,
  voiceChatInteractionPhase,
  voiceChatViewPatch,
  voiceAuthorizationChangePatch,
  voiceAuthorizationConsentPatch,
  voiceCaptureStatsPatch,
  voiceChatErrorPatch,
  voiceChatAutoListenGateSnapshot,
  voiceConnectionStatusPatch,
  voiceGatewayEventPatch,
  voiceSessionDetailPatch,
} = require("../components/voice-session/voice-chat-page-state.ts");

test("voice chat derives one interaction phase from overlapping runtime facts", () => {
  const state = (patch = {}) => ({
    ...initialVoiceChatData(),
    ...patch,
  });

  assert.equal(voiceChatInteractionPhase(state()), "idle");
  assert.equal(voiceChatInteractionPhase(state({ recording: true })), "recording");
  assert.equal(voiceChatInteractionPhase(state({
    latestTranscriptText: "我刚讲完的一段话。",
    followUpLoading: true,
    followUpTTSLoading: true,
  })), "transcript_processing");
  assert.equal(voiceChatInteractionPhase(state({
    followUpLoading: true,
    followUpTTSLoading: true,
  })), "assistant_preparing");
  assert.equal(voiceChatInteractionPhase(state({
    latestTranscriptText: "我刚讲完的一段话。",
    assistantPromptText: "那时候您最难忘的人是谁？",
    followUpLoading: true,
    followUpTTSPlaying: true,
  })), "assistant_responding");
  assert.equal(voiceChatInteractionPhase(state({
    latestTranscriptText: "我刚讲完的一段话。",
    followUpLoading: true,
    followUpTTSPlaying: true,
  })), "assistant_responding");
  assert.equal(voiceChatInteractionPhase(state({
    assistantPromptText: "那时候您最难忘的人是谁？",
  })), "waiting_for_answer");
  assert.equal(voiceChatInteractionPhase(state({
    followUpError: "小余回应失败",
    assistantPromptText: "上一轮留下的问题",
  })), "assistant_error");
});

test("recording level updates keep long history on the page without resending it", () => {
  const sent = [];
  const context = {
    data: initialVoiceChatData(),
    setData(patch) { sent.push(patch); Object.assign(this.data, patch); },
  };
  const lines = Array.from({ length: 500 }, (_, id) => ({ id: String(id), role: "user", text: "合成的口述文字。".repeat(20) }));
  applyVoiceChatDataPatch(context, { transcriptLines: lines });
  const renderedHistory = context.data.transcriptLineViews;
  applyVoiceChatDataPatch(context, { audioLevelPercent: 42 });
  assert.deepEqual(sent.at(-1), { audioLevelPercent: 42 });
  assert.equal(context.data.transcriptLineViews, renderedHistory);
  assert.equal(context.data.transcriptLineViews.length, 500);

  applyVoiceChatDataPatch(context, { interviewType: "human_interview" });
  assert.equal(context.data.transcriptLineViews[0].roleText, "被访者");
  applyVoiceChatDataPatch(context, { transcriptLines: [...lines, { id: "new", role: "user", text: "新讲述" }] });
  assert.equal(context.data.transcriptLineViews.at(-1).text, "新讲述");
});

test("voice chat initial data keeps the assistant ready for opening narration", () => {
  const first = initialVoiceChatData();
  const second = initialVoiceChatData();

  assert.equal(first.status, "idle");
  assert.equal(first.primaryActionText, "开始说话");
  assert.equal(first.autoStopEnabled, true);
  assert.equal(first.autoListenEnabled, true);
  assert.equal(first.largeTextEnabled, false);
  assert.equal(first.autoBargeInEnabled, true);
  assert.equal(first.voiceChatPageClass, "voice-chat-page");
  assert.equal(first.showVoiceAuthorizationPanel, true);
  assert.equal(first.showTranscriptStage, false);
  assert.equal(first.showAssistantActions, false);
  assert.equal(first.micButtonClass, "mic-button disabled");
  assert.equal(voiceChatViewPatch(first).micButtonClass, "mic-button disabled");
  assert.equal(first.micButtonDisabled, true);
  assert.equal(first.micButtonProcessing, false);
  assert.equal(first.micButtonText, "同意后开始说话");
  assert.equal(first.liveScrollClass, "live-scroll");
  assert.equal(first.liveScrollTop, 0);
  assert.equal(first.liveTranscriptFollowing, true);
  assert.equal(first.liveReviewStartCharCount, 0);
  assert.equal(first.showReturnToLatest, false);
  assert.equal(first.returnToLatestText, "回到最新");
  assert.equal(first.historyCountText, "0 段 · 0 字");
  assert.equal(first.historyExpanded, false);
  assert.equal(first.showHistoryPanel, false);
  assert.equal(first.showHistoryLines, false);
  assert.deepEqual(first.transcriptLines, []);
  assert.deepEqual(first.transcriptLineViews, []);
  assert.equal(first.openingTopicOptions.length, 5);
  assert.deepEqual(first.openingTopicOptions.map((item) => item.label), [
    "家人",
    "工作",
    "朋友",
    "一个地方",
    "随便聊聊",
  ]);
  assert.notEqual(first.transcriptLines, second.transcriptLines);
  assert.notEqual(first.transcriptLineViews, second.transcriptLineViews);
  assert.notEqual(first.ttsAlignment, second.ttsAlignment);
  assert.notEqual(first.openingTopicOptions, second.openingTopicOptions);
});

test("voice chat view patch derives simple fields for first assistant turn", () => {
  const data = {
    ...initialVoiceChatData(),
    voiceAuthorizationAgreed: true,
    projectTitle: "外婆的故事",
    followUpLoading: true,
    followUpTTSLoading: true,
  };
  const patch = voiceChatViewPatch(data);

  assert.equal(patch.topicTitleText, "外婆的故事");
  assert.equal(patch.voiceChatPageClass, "voice-chat-page");
  assert.equal(patch.showVoiceAuthorizationPanel, false);
  assert.equal(patch.showAssistantPanel, true);
  assert.equal(patch.showAssistantOpening, false);
  assert.equal(patch.showTranscriptStage, false);
  assert.equal(patch.showAssistantActions, true);
  assert.equal(patch.assistantDisplayText, "小余正在准备第一句话");
  assert.equal(patch.stageStatusText, "小余正在准备问题");
  assert.equal(patch.liveDisplayText, "小余正在准备第一句话");
  assert.equal(patch.controlHintText, "小余先问一句，稍等一下");
  assert.equal(patch.micButtonText, "我来讲了");
  assert.equal(patch.micButtonDisabled, false);
  assert.equal(patch.micButtonProcessing, false);
});

test("voice chat view patch derives transcript and recording render fields", () => {
  const data = {
    ...initialVoiceChatData(),
    voiceAuthorizationAgreed: true,
    recording: true,
    liveText: "",
    liveCharCount: 8,
    canRequestFollowUp: true,
    transcriptLines: [
      { id: "assistant-1", role: "assistant", text: "先从哪段开始讲？" },
      { id: "user-1", role: "user", text: "我想讲外婆。" },
    ],
    totalTranscriptCharCount: 7,
  };
  const patch = voiceChatViewPatch(data);

  assert.equal(patch.voiceChatPageClass, "voice-chat-page ai-recording-focus");
  assert.equal(patch.showAssistantPanel, false);
  assert.equal(patch.showTranscriptStage, true);
  assert.equal(patch.showAssistantActions, false);
  assert.equal(patch.stageStatusClass, "stage-status listening");
  assert.equal(patch.stageStatusText, "我在听，您慢慢说");
  assert.equal(patch.liveDisplayText, "直接说就行，我会帮您记下来");
  assert.equal(patch.liveTextClass, "live-text");
  assert.equal(patch.captureStripClass, "capture-strip active");
  assert.equal(patch.showLiveCharCount, true);
  assert.equal(patch.showInlineHelp, true);
  assert.equal(patch.historyCountText, "1 段 · 6 字");
  assert.equal(patch.showHistoryPanel, true);
  assert.equal(patch.showHistoryLines, false);
  assert.equal(patch.historyToggleText, "查看");
  assert.deepEqual(patch.transcriptLineViews.map((line) => [line.lineClass, line.roleText]), [
    ["transcript-line user", "我"],
  ]);
  assert.equal(patch.controlHintText, "说完请点这里");
  assert.equal(patch.micButtonClass, "mic-button recording");
  assert.equal(patch.micButtonText, "说完了，帮我整理");
});

test("voice chat only expands saved history when the user asks to review it", () => {
  const lines = [
    { id: "user-1", role: "user", text: "我想讲外婆。" },
  ];
  const collapsed = voiceChatViewPatch({
    ...initialVoiceChatData(),
    transcriptLines: lines,
    totalTranscriptCharCount: 7,
  });
  const expanded = voiceChatViewPatch({
    ...initialVoiceChatData(),
    transcriptLines: lines,
    totalTranscriptCharCount: 7,
    historyExpanded: true,
  });

  assert.equal(collapsed.showHistoryPanel, true);
  assert.equal(collapsed.showHistoryLines, false);
  assert.equal(collapsed.historyToggleText, "查看");
  assert.equal(expanded.showHistoryLines, true);
  assert.equal(expanded.historyToggleText, "收起");
});

test("voice chat does not treat an assistant-only opening prompt as saved narration", () => {
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    assistantPromptText: "今天想先讲哪件小事？",
    transcriptLines: [
      { id: "assistant-1", role: "assistant", text: "今天想先讲哪件小事？" },
    ],
  });

  assert.equal(patch.showHistoryPanel, false);
  assert.equal(patch.showHistoryLines, false);
});

test("voice chat keeps the saved transcript as the only focus while the next question is prepared", () => {
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    voiceAuthorizationAgreed: true,
    latestTranscriptText: "那个月亮特别大，也特别圆。",
    liveText: "那个月亮特别大，也特别圆。",
    followUpLoading: true,
    canRequestFollowUp: true,
  });

  assert.equal(patch.showAssistantPanel, false);
  assert.equal(patch.showTranscriptStage, true);
  assert.equal(patch.showAssistantActions, false);
  assert.equal(patch.stageStatusText, "正在帮您整理成文字");
  assert.equal(patch.liveDisplayText, "那个月亮特别大，也特别圆。");
  assert.equal(patch.controlHintText, "正在帮您整理刚才的故事");
  assert.equal(patch.micButtonClass, "mic-button processing disabled");
  assert.equal(patch.micButtonDisabled, true);
  assert.equal(patch.micButtonProcessing, true);
  assert.equal(patch.micButtonText, "正在帮您整理");
});

test("voice chat switches to the assistant as soon as the next question starts playing", () => {
  const question = "您刚才说到可乐，第一次喝到它是在什么时候？";
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    voiceAuthorizationAgreed: true,
    latestTranscriptText: "这是上一轮已经保存的一大段讲述。",
    liveText: "这是上一轮已经保存的一大段讲述。",
    assistantPromptText: question,
    followUpLoading: true,
    followUpTTSLoading: false,
    followUpTTSPlaying: true,
  });

  assert.equal(patch.showAssistantPanel, true);
  assert.equal(patch.showTranscriptStage, false);
  assert.equal(patch.assistantDisplayText, question);
  assert.equal(patch.showAssistantTTS, true);
  assert.equal(patch.stageStatusText, "小余正在读问题");
  assert.equal(patch.controlHintText, "听完就可以开始说");
  assert.equal(patch.micButtonClass, "mic-button");
  assert.equal(patch.micButtonDisabled, false);
  assert.equal(patch.micButtonProcessing, false);
  assert.equal(patch.micButtonText, "我来讲了");
});

test("voice chat keeps one continuous live transcript and exposes paused follow state", () => {
  const liveText = "小时候我们住在河边，每天放学以后都会先去找外婆。后来搬到了城里，但我一直记得院子里的桂花树，秋天的时候整个屋子都能闻到香味。";
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    recording: true,
    liveText,
    liveCharCount: liveText.length,
    liveScrollClass: "live-scroll long",
    liveTranscriptFollowing: false,
    liveReviewStartCharCount: liveText.length - 5,
  });

  assert.equal(patch.liveCurrentText, liveText);
  assert.match(patch.liveScrollClass, /reviewing/);
  assert.equal(patch.showReturnToLatest, true);
  assert.equal(patch.returnToLatestText, "回到最新 · 新增 5 字");
});

test("voice chat view patch combines human speaker choice with record actions", () => {
  const idlePatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    interviewType: "human_interview",
    voiceAuthorizationAgreed: true,
    speakerRole: "interviewer",
  });
  const recordingPatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    interviewType: "human_interview",
    voiceAuthorizationAgreed: true,
    speakerRole: "interviewer",
    recording: true,
  });
  const startingPatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    interviewType: "human_interview",
    voiceAuthorizationAgreed: true,
    recordingStarting: true,
    connecting: true,
  });
  const authorizationPatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    interviewType: "human_interview",
    voiceAuthorizationAgreed: false,
  });

  assert.equal(idlePatch.showHumanRecordActions, true);
  assert.equal(idlePatch.showControlBar, false);
  assert.equal(idlePatch.voiceChatPageClass, "voice-chat-page human-interview no-control-bar");
  assert.equal(idlePatch.showStageStatus, false);
  assert.equal(idlePatch.showCaptureStrip, false);
  assert.equal(idlePatch.controlHintText, "");
  assert.equal(idlePatch.liveDisplayText, "点上方开始记录采访者");
  assert.equal(idlePatch.narratorRecordButtonClass, "role-record-button narrator");
  assert.equal(idlePatch.interviewerRecordButtonClass, "role-record-button interviewer");
  assert.equal(startingPatch.showHumanRecordActions, false);
  assert.equal(startingPatch.showControlBar, true);
  assert.equal(startingPatch.micButtonDisabled, true);
  assert.equal(startingPatch.micButtonText, "正在打开麦克风");
  assert.equal(recordingPatch.showHumanRecordActions, false);
  assert.equal(recordingPatch.showControlBar, true);
  assert.equal(recordingPatch.voiceChatPageClass, "voice-chat-page human-interview human-recording-focus");
  assert.equal(recordingPatch.showStageStatus, false);
  assert.equal(recordingPatch.showCaptureStrip, true);
  assert.equal(recordingPatch.controlHintText, "");
  assert.equal(recordingPatch.liveDisplayText, "等待转写...");
  assert.equal(recordingPatch.liveTextClass, "live-text placeholder");
  assert.equal(recordingPatch.micButtonText, "说完了，保存这段");
  assert.equal(authorizationPatch.showHumanRecordActions, false);
  assert.equal(authorizationPatch.showControlBar, true);
  assert.equal(authorizationPatch.showStageStatus, false);
  assert.equal(authorizationPatch.showCaptureStrip, false);
  assert.equal(authorizationPatch.micButtonText, "同意后开始说话");
});

test("book completion renders one issue without disabling recording controls", () => {
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    sessionGoal: "book_completion",
    voiceAuthorizationAgreed: true,
    assistantPromptText: "模型生成的另一个问法",
    completionIssueAction: "deferred",
    completionIssue: {
      id: "issue-1",
      question: "爷爷退休以后为什么还一直忙公司的事？",
      status: "in_progress",
    },
  });

  assert.equal(patch.showCompletionIssuePanel, true);
  assert.equal(patch.topSubtitle, "一次只补一个重点");
  assert.equal(patch.showAssistantPanel, true);
  assert.equal(patch.showAssistantOpening, false);
  assert.equal(patch.assistantDisplayText, "模型生成的另一个问法");
  assert.equal(patch.completionIssueQuestionText, "爷爷退休以后为什么还一直忙公司的事？");
  assert.equal(patch.showCompletionIssueActions, true);
  assert.equal(patch.completionIssueControlsDisabled, true);
  assert.equal(patch.micButtonDisabled, false);
  assert.equal(patch.finishButtonText, "保存离开");
});

test("book completion without an issue exposes the invalid session and disables recording", () => {
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    sessionGoal: "book_completion",
    voiceAuthorizationAgreed: true,
    completionIssue: null,
    completionIssueLoading: false,
  });

  assert.equal(patch.completionIssueStatusText, "没有拿到本次要补的问题");
  assert.equal(patch.completionIssueQuestionText, "本次问题没有准备好");
  assert.equal(patch.liveDisplayText, "请保存离开后，从首页重新进入");
  assert.equal(patch.showCompletionIssueActions, false);
  assert.equal(patch.showAssistantPanel, false);
  assert.equal(patch.micButtonDisabled, true);
  assert.equal(patch.micButtonText, "请先保存离开");
});

test("closed book completion returns to the whole-book overview", () => {
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    sessionGoal: "book_completion",
    completionIssue: {
      id: "issue-1",
      question: "请按顺序讲讲这段经历。",
      status: "closed",
      outcome: "deferred",
    },
  });

  assert.equal(patch.showCompletionIssueContinue, true);
  assert.equal(patch.completionIssueStatusText, "本次补充已经结束");
  assert.equal(patch.showControlBar, false);
  assert.equal(patch.showAssistantActions, false);
});

test("answered book completion ends recording flow before background verification finishes", () => {
  const patch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    sessionGoal: "book_completion",
    voiceAuthorizationAgreed: true,
    assistantExpectsAnswer: false,
    assistantPromptText: "这段背景已经保存，接下来可以返回全书概览。",
    completionIssue: {
      id: "issue-1",
      question: "这段内容开始前，读者还需要知道哪些重要背景？",
      status: "in_progress",
    },
    transcriptLines: [
      {
        id: "answer-1",
        role: "user",
        text: "今年看到山桃花照片以后，我们重新联系了。",
      },
    ],
  });

  assert.equal(patch.showControlBar, false);
  assert.equal(patch.showCaptureStrip, false);
  assert.equal(patch.micButtonDisabled, true);
  assert.equal(patch.showCompletionIssueActions, false);
  assert.equal(patch.showCompletionIssueContinue, true);
  assert.equal(patch.showAssistantActions, false);
  assert.equal(patch.stageStatusText, "本次补充已经完成");
  assert.equal(patch.liveDisplayText, "回答已经保存，可以返回全书概览。");
  assert.equal(patch.assistantLabelText, "本次补充已结束");
});

test("voice chat view patch adds focused large text root classes", () => {
  const idlePatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    largeTextEnabled: true,
  });
  const assistantPatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    largeTextEnabled: true,
    voiceAuthorizationAgreed: true,
    assistantPromptText: "爷爷退休以后还在忙什么？",
  });
  const capturePatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    largeTextEnabled: true,
    voiceAuthorizationAgreed: true,
    recording: true,
    liveText: "我想先讲爷爷退休后的事",
  });
  const humanCapturePatch = voiceChatViewPatch({
    ...initialVoiceChatData(),
    interviewType: "human_interview",
    largeTextEnabled: true,
    voiceAuthorizationAgreed: true,
    recording: true,
    liveText: "我小时候家里屋顶很低",
  });

  assert.equal(idlePatch.voiceChatPageClass, "voice-chat-page large-text idle-focus");
  assert.equal(assistantPatch.voiceChatPageClass, "voice-chat-page large-text assistant-focus");
  assert.equal(assistantPatch.liveDisplayText, "现在可以说了");
  assert.equal(capturePatch.voiceChatPageClass, "voice-chat-page ai-recording-focus large-text capture-focus");
  assert.equal(capturePatch.liveDisplayText, "我想先讲爷爷退休后的事");
  assert.equal(humanCapturePatch.voiceChatPageClass, "voice-chat-page human-interview human-recording-focus large-text capture-focus");
  assert.equal(humanCapturePatch.showStageStatus, false);
  assert.equal(humanCapturePatch.liveTextClass, "live-text");
});

test("voice chat auto-listen gate snapshot keeps page and computed gate state together", () => {
  assert.deepEqual(
    voiceChatAutoListenGateSnapshot(
      {
        autoListenEnabled: true,
        status: "ready",
        recording: false,
        connecting: false,
        followUpLoading: false,
      },
      {
        hasClient: true,
        pendingAfterPlayback: true,
        canStart: true,
      },
    ),
    {
      hasClient: true,
      autoListenEnabled: true,
      status: "ready",
      recording: false,
      connecting: false,
      followUpLoading: false,
      pendingAfterPlayback: true,
      canStart: true,
    },
  );
});

test("voice chat authorization selectors keep consent text consistent", () => {
  assert.deepEqual(
    voiceAuthorizationConsentPatch({ primaryActionText: "同意后开始说话", recording: false }, true),
    {
      voiceAuthorizationAgreed: true,
      primaryActionText: "开始说话",
      error: "",
      autoListenStateText: "小余说完自动听",
    },
  );
  assert.deepEqual(
    voiceAuthorizationChangePatch({ recording: true, error: "旧错误" }, false),
    {
      voiceAuthorizationAgreed: false,
      recording: false,
      error: "旧错误",
      primaryActionText: "同意后开始说话",
      autoListenStateText: "同意后才会自动收音",
    },
  );
});

test("voice chat session detail selector applies saved and assistant state", () => {
  const data = initialVoiceChatData();
  const patch = voiceSessionDetailPatch({
    session: {
      status: "stopped",
      guidance_mode: "guided_interview",
      current_guidance_module: "person",
    },
    messages: [
      {
        id: "message-user",
        role: "user",
        content_text: "我想讲外婆",
        turn_index: 1,
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "message-assistant",
        role: "assistant",
        content_text: "外婆最常做的一件事是什么？",
        turn_index: 2,
        created_at: "2026-01-01T00:00:01Z",
        audio_file_id: "audio-assistant",
        metadata_json: { expects_answer: true },
      },
    ],
    utterances: [
      {
        id: "utterance-1",
        chat_message_id: "message-user",
        transcript_raw: "我想讲外婆",
        transcript_edited: "",
        created_at: "2026-01-01T00:00:00Z",
        audio_file_id: "audio-user",
      },
    ],
  }, data);

  assert.equal(patch.status, "closed");
  assert.equal(patch.primaryActionText, "已保存");
  assert.equal(patch.currentGuidanceModule, "person");
  assert.equal(patch.latestTranscriptText, "我想讲外婆");
  assert.equal(patch.assistantPromptText, "外婆最常做的一件事是什么？");
  assert.equal(patch.followUpAudioFileId, "audio-assistant");
});

test("voice session detail selector carries the canonical completion issue binding", () => {
  const data = initialVoiceChatData();
  const patch = voiceSessionDetailPatch({
    session: {
      project_id: "project-1",
      status: "created",
      guidance_mode: "guided_interview",
      interview_type: "human_interview",
      session_goal: "book_completion",
      current_completion_issue_id: "issue-1",
      error_message: "",
    },
    messages: [],
    utterances: [],
  }, data);

  assert.equal(patch.projectId, "project-1");
  assert.equal(patch.sessionGoal, "book_completion");
  assert.equal(patch.currentCompletionIssueId, "issue-1");
});

test("voice chat realtime selectors derive capture, gateway, status, and error patches", () => {
  assert.deepEqual(voiceConnectionStatusPatch("listening", { recording: false }), {
    status: "listening",
    statusText: "正在听",
    recording: true,
  });

  const capture = voiceCaptureStatsPatch({
    state: "recording",
    frameCount: 12,
    audioBytesSent: 3072,
    recordingElapsedMs: 61000,
    silentElapsedMs: 4000,
    audioLevel: 0.25,
    gatewayState: "listening",
    autoStopped: true,
    autoStopReason: "no_voice",
  }, {
    autoStopEnabled: true,
    noAnswerPromptAlreadyHandled: false,
  });
  assert.equal(capture.shouldHandleNoAnswerAutoStop, true);
  assert.equal(capture.patch.captureDurationText, "01:01");
  assert.equal(capture.patch.primaryActionText, "继续说话");

  assert.deepEqual(
    voiceGatewayEventPatch({ type: "audio.ended", state: "finishing" }, { persistenceStateText: "识别中" }),
    {
      gatewayStateText: "识别保存中",
      recording: false,
      primaryActionText: "继续说话",
      persistenceStateText: "正在保存",
    },
  );

  const errorPatch = voiceChatErrorPatch(
    "连接断开",
    { kind: "connection", state: "failed", message: "连接断开" },
    { persistenceStateText: "识别中", recoveryNotice: "" },
  );
  assert.equal(errorPatch.status, "error");
  assert.equal(errorPatch.errorKindText, "连接错误");
  assert.equal(errorPatch.gatewayStateText, "连接异常");
  assert.equal(errorPatch.canReconnect, true);
});
