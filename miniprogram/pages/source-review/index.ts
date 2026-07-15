import type {
  MemoirBookSourceReviewConfirmRequest,
  MemoirBookSourceReviewItem,
  MemoirFollowupAnswerKind,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import {
  confirmMemoirBookSourceReviewItem,
  createMemoirVoiceSession,
  listMemoirBookSourceReviewItems,
} from "../../services/memoir-voice-api";
import { getMemoirFollowupTask } from "../../services/memoir-followup-task-api";
import { routeAuthError, switchToBook } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import {
  buildSourceReviewConfirmationRequest,
  type MixedAnswerParts,
} from "./source-review-form";


const ANSWER_KINDS = ["direct_narrative", "clarification", "fact_resolution", "mixed"] as const;

interface AnswerChoice {
  kind: MemoirFollowupAnswerKind;
  title: string;
  detail: string;
}

interface SourceReviewData extends MixedAnswerParts {
  projectId: string;
  eventId: string;
  loading: boolean;
  saving: boolean;
  startingVoice: boolean;
  success: boolean;
  error: string;
  questionText: string;
  taskId: string;
  confirmationType: "answer_usage" | "fact_resolution";
  sourceText: string;
  selectedKind: MemoirFollowupAnswerKind | "";
  choices: AnswerChoice[];
}

interface ChoiceDataset {
  kind?: string;
}

interface MixedFieldDataset {
  field?: string;
}

type ChoiceEvent = PageDatasetEvent<ChoiceDataset>;
type MixedFieldEvent = PageTextInputEvent<MixedFieldDataset>;

const EMPTY_MIXED_PARTS: MixedAnswerParts = {
  directNarrativeText: "",
  clarificationText: "",
  factResolutionText: "",
};

const CHOICES: AnswerChoice[] = [
  {
    kind: "direct_narrative",
    title: "作为正文素材",
    detail: "这段话可以直接写进章节正文",
  },
  {
    kind: "clarification",
    title: "解释原文意思",
    detail: "只帮助理解已有内容，不直接写进正文",
  },
  {
    kind: "fact_resolution",
    title: "确认或纠正事实",
    detail: "用于核对时间、人物或事件是否准确",
  },
  {
    kind: "mixed",
    title: "里面有两种内容",
    detail: "把能写进正文和只作说明的原话分开",
  },
];


function reviewItemPatch(item: MemoirBookSourceReviewItem): Pick<
  SourceReviewData,
  "questionText" | "sourceText" | "taskId" | "confirmationType"
> {
  const followup = item.followup_answer;
  return {
    questionText: followup?.confirmation_question.trim()
      || item.summary_text.trim()
      || "这段话是要写进书里，还是只用来解释已有内容？",
    sourceText: item.source_text.trim(),
    taskId: followup?.task_id || "",
    confirmationType: followup?.confirmation_type || "answer_usage",
  };
}


definePage<SourceReviewData>()({
  data: {
    projectId: "",
    eventId: "",
    loading: true,
    saving: false,
    startingVoice: false,
    success: false,
    error: "",
    questionText: "",
    taskId: "",
    confirmationType: "answer_usage",
    sourceText: "",
    selectedKind: "",
    choices: CHOICES,
    ...EMPTY_MIXED_PARTS,
  },

  async onLoad(options: PageQuery) {
    this.setData({
      projectId: queryParam(options, "projectId"),
      eventId: queryParam(options, "eventId"),
    });
    await this.loadReviewItem();
  },

  async loadReviewItem() {
    if (!this.data.projectId || !this.data.eventId) {
      this.setData({ loading: false, error: "没有找到要确认的回答" });
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const response = await listMemoirBookSourceReviewItems(this.data.projectId);
      const item = response.items.find((candidate) => candidate.id === this.data.eventId);
      if (!item) {
        this.setData({
          loading: false,
          error: "这项确认已经处理，请返回书稿查看最新状态",
        });
        return;
      }
      if (item.followup_answer?.status !== "needs_confirmation") {
        this.setData({ loading: false, error: "这条讲述不需要确认回答用途" });
        return;
      }
      const patch = reviewItemPatch(item);
      if (!patch.sourceText) {
        this.setData({
          ...patch,
          loading: false,
          error: "没有找到这段回答的原文，暂时无法确认",
        });
        return;
      }
      this.setData({
        ...patch,
        loading: false,
        error: "",
      });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "回答加载失败",
      });
    }
  },

  selectAnswerKind(event: ChoiceEvent) {
    if (this.data.saving) {
      return;
    }
    const rawKind = datasetString(event, "kind") as MemoirFollowupAnswerKind;
    if (!ANSWER_KINDS.includes(rawKind)) {
      return;
    }
    const selectedKind = rawKind;
    this.setData({
      selectedKind,
      error: "",
      ...(selectedKind === "mixed" ? {} : EMPTY_MIXED_PARTS),
    });
  },

  updateMixedPart(event: MixedFieldEvent) {
    const field = datasetString(event, "field");
    if (!Object.prototype.hasOwnProperty.call(EMPTY_MIXED_PARTS, field)) {
      return;
    }
    this.setData({
      [field]: event.detail.value,
      error: "",
    } as Partial<SourceReviewData>);
  },

  async submitConfirmation() {
    if (this.data.saving || this.data.success) {
      return;
    }
    let request: MemoirBookSourceReviewConfirmRequest;
    try {
      request = buildSourceReviewConfirmationRequest(
        this.data.sourceText,
        this.data.selectedKind,
        {
          directNarrativeText: this.data.directNarrativeText,
          clarificationText: this.data.clarificationText,
          factResolutionText: this.data.factResolutionText,
        },
      );
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "请检查选择" });
      return;
    }
    this.setData({ saving: true, error: "" });
    try {
      await confirmMemoirBookSourceReviewItem(
        this.data.projectId,
        this.data.eventId,
        request,
      );
      this.setData({ saving: false, success: true });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        saving: false,
        error: error instanceof Error ? error.message : "确认失败",
      });
    }
  },

  async startFactConfirmation() {
    if (this.data.startingVoice || !this.data.projectId || !this.data.taskId) {
      return;
    }
    this.setData({ startingVoice: true, error: "" });
    try {
      const task = await getMemoirFollowupTask(this.data.projectId, this.data.taskId);
      if (task.status === "in_progress" && task.assigned_session_id) {
        wx.navigateTo({
          url: pageUrl("/pages/voice-chat/index", {
            voiceSessionId: task.assigned_session_id,
            mode: "guided_interview",
            interviewType: "ai_interview",
            sessionGoal: "book_completion",
            projectTitle: "补全本章",
          }),
        });
        return;
      }
      if (task.status !== "pending" && task.status !== "deferred") {
        this.setData({ error: "这个问题当前不能继续，请返回章节工作台刷新状态" });
        return;
      }
      if (!readVoiceAuthorizationConsent()) {
        wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
        return;
      }
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "book_completion",
        current_followup_task_id: this.data.taskId,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_source_review_fact_confirmation",
          source_review_event_id: this.data.eventId,
        },
      }, { projectId: this.data.projectId });
      wx.navigateTo({
        url: pageUrl("/pages/voice-chat/index", {
          voiceSessionId: detail.session.id,
          mode: "guided_interview",
          interviewType: "ai_interview",
          sessionGoal: "book_completion",
          projectTitle: "补全本章",
        }),
      });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "AI 访谈创建失败",
      });
    } finally {
      this.setData({ startingVoice: false });
    }
  },

  returnToBook() {
    switchToBook();
  },
});
