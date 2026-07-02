import type {
  MemoirBookPlan,
  MemoirVoiceTranscriptItem,
} from "../../domain/voice-chat";
import type { ChapterPickerOption } from "../../services/memoir-book-source-actions";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  assignMemoirBookSources,
  deleteMemoirVoiceTranscript,
  getMemoirBookPlan,
  getMemoirVoiceTranscript,
  moveMemoirBookSource,
  previewMemoirVoiceTranscriptVoiceEdit,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-voice-api";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  sourceAssignmentRequest,
  transcriptChapterAssignOptions,
  transcriptChapterMoveOptions,
  transcriptSourceMoveRequest,
} from "../../services/memoir-book-source-actions";
import { ApiError } from "../../services/request";
import {
  isAuthError,
  routeAuthError,
  switchToMe,
} from "../../services/navigation";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";
import {
  plainTranscriptSegments,
  transcriptAudioText,
  transcriptBookInclusionText,
  transcriptCreatedAtText,
  transcriptTitleText,
  transcriptVoiceEditPreviewView,
  type TranscriptTextSegment,
  type TranscriptVoiceEditPreviewView,
} from "../../services/transcript-view-model";
import {
  pickerIndex,
  queryParam,
  type PagePickerEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";

type TextAreaEvent = PageTextInputEvent;
type PickerEvent = PagePickerEvent;

interface TranscriptDetailItemView extends MemoirVoiceTranscriptItem {
  titleText: string;
  editingText: string;
  editingCharCount: number;
  createdAtText: string;
  audioText: string;
  recordMetaText: string;
  bookInclusionText: string;
  isIncludedInBook: boolean;
  canAssignChapterSource: boolean;
  chapterAssignOptions: ChapterPickerOption[];
  canMoveChapterSource: boolean;
  chapterMoveOptions: ChapterPickerOption[];
  canPlayAudio: boolean;
  questionCanExpand: boolean;
  questionSegments: TranscriptTextSegment[];
  rawSegments: TranscriptTextSegment[];
  displaySegments: TranscriptTextSegment[];
  changed: boolean;
  isEditing: boolean;
}

interface TranscriptDetailData {
  loading: boolean;
  needsLogin: boolean;
  saving: boolean;
  deleting: boolean;
  assigning: boolean;
  moving: boolean;
  playing: boolean;
  error: string;
  utteranceId: string;
  item: TranscriptDetailItemView | null;
  bookPlan: MemoirBookPlan | null;
  questionExpanded: boolean;
  moreActionsExpanded: boolean;
  voiceEditRecording: boolean;
  voiceEditLoading: boolean;
  voiceEditSaving: boolean;
  voiceEditInstructionText: string;
  voiceEditStatusText: string;
  voiceEditPreview: TranscriptVoiceEditPreviewView | null;
}

const COLLAPSIBLE_QUESTION_CHAR_COUNT = 72;

async function loadTranscriptBookPlan(projectId: string): Promise<MemoirBookPlan | null> {
  try {
    return await getMemoirBookPlan(projectId);
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) {
      return null;
    }
    throw error;
  }
}

function toViewItem(
  item: MemoirVoiceTranscriptItem,
  bookPlan: MemoirBookPlan | null,
  previous?: TranscriptDetailItemView | null,
): TranscriptDetailItemView {
  const editingText = item.transcript_edited || item.transcript_raw;
  const isIncludedInBook = Boolean(item.is_included_in_book || item.included_chapters?.length);
  const chapterAssignOptions = transcriptChapterAssignOptions(item, bookPlan);
  const chapterMoveOptions = transcriptChapterMoveOptions(item, bookPlan);
  const createdAtText = transcriptCreatedAtText(item);
  const audioText = transcriptAudioText(item);
  const questionText = item.question_text.trim();
  return {
    ...item,
    titleText: transcriptTitleText(item),
    editingText: previous?.isEditing ? previous.editingText : editingText,
    editingCharCount: (previous?.isEditing ? previous.editingText : editingText).trim().length,
    createdAtText,
    audioText,
    recordMetaText: [createdAtText, audioText].filter(Boolean).join(" · "),
    bookInclusionText: transcriptBookInclusionText(item),
    isIncludedInBook,
    canAssignChapterSource: chapterAssignOptions.length > 0,
    chapterAssignOptions,
    canMoveChapterSource: chapterMoveOptions.length > 0,
    chapterMoveOptions,
    canPlayAudio: Boolean(item.audio_file_id || item.question_audio_file_id),
    questionCanExpand: Array.from(questionText).length > COLLAPSIBLE_QUESTION_CHAR_COUNT,
    questionSegments: plainTranscriptSegments(item.question_text),
    rawSegments: plainTranscriptSegments(item.transcript_raw),
    displaySegments: plainTranscriptSegments(item.display_text),
    changed: previous?.isEditing
      ? previous.editingText.trim() !== editingText.trim()
      : false,
    isEditing: Boolean(previous?.isEditing),
  };
}

function changedItem(item: TranscriptDetailItemView, editingText: string): TranscriptDetailItemView {
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    changed: editingText.trim() !== (item.transcript_edited || item.transcript_raw).trim(),
    isEditing: true,
  };
}

function confirmDelete(item: TranscriptDetailItemView): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = item.titleText || item.display_text || item.transcript_raw || "这条讲述记录";
    wx.showModal({
      title: "删除讲述记录",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后，这条原始讲述会从素材库移除；如果已放入章节，相关章节会标记为需重新确认，已生成正文不会自动改写。`,
      confirmText: "删除",
      confirmColor: "#b84a5a",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

Page<TranscriptDetailData, {
  onLoad(options?: PageQuery): Promise<void>;
  loadTranscript(): Promise<void>;
  beginEdit(): void;
  toggleQuestionExpanded(): void;
  toggleMoreActionsExpanded(): void;
  changeText(event: TextAreaEvent): void;
  cancelEdit(): void;
  saveTranscript(): Promise<void>;
  resetToRaw(): void;
  startVoiceEdit(): Promise<void>;
  cancelVoiceEdit(): void;
  confirmVoiceEdit(): Promise<void>;
  assignTranscriptChapter(event: PickerEvent): Promise<void>;
  moveTranscriptChapter(event: PickerEvent): Promise<void>;
  deleteTranscript(): Promise<void>;
  playAudio(): Promise<void>;
  stopPlayback(): void;
  goBack(): void;
  openLogin(): void;
  onUnload(): void;
  audioPlayback: AudioPlaybackController;
  loadToken: number;
  voiceEditToken: number;
}>({
  audioPlayback: new AudioPlaybackController(),
  loadToken: 0,
  voiceEditToken: 0,

  data: {
    loading: false,
    needsLogin: false,
    saving: false,
    deleting: false,
    assigning: false,
    moving: false,
    playing: false,
    error: "",
    utteranceId: "",
    item: null,
    bookPlan: null,
    questionExpanded: false,
    moreActionsExpanded: false,
    voiceEditRecording: false,
    voiceEditLoading: false,
    voiceEditSaving: false,
    voiceEditInstructionText: "",
    voiceEditStatusText: "",
    voiceEditPreview: null,
  },

  async onLoad(options) {
    const utteranceId = queryParam(options, "id");
    this.setData({
      utteranceId,
      questionExpanded: false,
      moreActionsExpanded: false,
    });
    await this.loadTranscript();
    if (queryParam(options, "mode") === "edit") {
      this.beginEdit();
    }
  },

  async loadTranscript() {
    if (!this.data.utteranceId) {
      this.setData({ error: "缺少讲述记录" });
      return;
    }
    this.loadToken += 1;
    const requestToken = this.loadToken;
    this.setData({ loading: true, error: "" });
    try {
      const transcript = await getMemoirVoiceTranscript(this.data.utteranceId);
      const bookPlan = await loadTranscriptBookPlan(transcript.project_id);
      if (requestToken !== this.loadToken) {
        return;
      }
      this.setData({
        needsLogin: false,
        item: toViewItem(transcript, bookPlan, this.data.item),
        bookPlan,
      });
      trackAuditAction("transcript.detail_view", {}, { projectId: transcript.project_id });
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      if (isAuthError(error)) {
        this.setData({ needsLogin: true, item: null, bookPlan: null, error: "" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "讲述记录加载失败" });
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ loading: false });
      }
    }
  },

  beginEdit() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    const current = item.transcript_edited || item.transcript_raw;
    this.setData({
      item: {
        ...item,
        editingText: current,
        editingCharCount: current.trim().length,
        changed: false,
        isEditing: true,
      },
      error: "",
    });
  },

  toggleQuestionExpanded() {
    if (!this.data.item?.questionCanExpand) {
      return;
    }
    this.setData({ questionExpanded: !this.data.questionExpanded });
  },

  toggleMoreActionsExpanded() {
    this.setData({ moreActionsExpanded: !this.data.moreActionsExpanded });
  },

  changeText(event) {
    const item = this.data.item;
    if (!item) {
      return;
    }
    this.setData({ item: changedItem(item, event.detail.value) });
  },

  cancelEdit() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    this.setData({ item: toViewItem(item, this.data.bookPlan), error: "" });
  },

  async saveTranscript() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    const content = item.editingText.trim();
    if (!content) {
      this.setData({ error: "成书文字不能为空" });
      return;
    }
    this.setData({ saving: true, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(item.id, content);
      this.setData({ item: toViewItem(updated, this.data.bookPlan) });
      trackAuditAction("transcript.detail_edit_save", {}, { projectId: updated.project_id });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
    } finally {
      this.setData({ saving: false });
    }
  },

  resetToRaw() {
    const item = this.data.item;
    if (!item) {
      return;
    }
    this.setData({ item: changedItem(item, item.transcript_raw) });
  },

  async startVoiceEdit() {
    const item = this.data.item;
    if (
      !item
      || this.data.saving
      || this.data.deleting
      || this.data.voiceEditLoading
      || this.data.voiceEditSaving
    ) {
      return;
    }
    if (this.data.voiceEditRecording) {
      stopMemoirVoiceSearchRecording();
      return;
    }
    this.stopPlayback();
    this.voiceEditToken += 1;
    const requestToken = this.voiceEditToken;
    this.setData({
      voiceEditRecording: true,
      voiceEditLoading: false,
      voiceEditSaving: false,
      voiceEditInstructionText: "",
      voiceEditStatusText: "正在听你说修改意见",
      voiceEditPreview: null,
      error: "",
    });
    try {
      const instructionText = await recordMemoirVoiceSearchQuery({
        onRecordingEnd: () => {
          if (requestToken !== this.voiceEditToken) {
            return;
          }
          this.setData({
            voiceEditRecording: false,
            voiceEditLoading: true,
            voiceEditStatusText: "正在识别修改意见",
          });
        },
        emptyTranscriptMessage: "没有识别到修改意见，请再说一次",
        noVoiceTranscriptMessage: "没有听到修改意见，请再说一次",
      });
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        voiceEditInstructionText: instructionText,
        voiceEditStatusText: "正在生成修改预览",
      });
      const preview = await previewMemoirVoiceTranscriptVoiceEdit(item.id, instructionText);
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        voiceEditPreview: transcriptVoiceEditPreviewView(preview),
        voiceEditStatusText: preview.action === "revise_transcript" ? "请确认修改内容" : preview.reply_text,
      });
      trackAuditAction("transcript.detail_voice_edit_preview", { action: preview.action }, { projectId: item.project_id });
    } catch (error) {
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音修改失败",
        voiceEditStatusText: "",
      });
    } finally {
      if (requestToken === this.voiceEditToken) {
        this.setData({
          voiceEditRecording: false,
          voiceEditLoading: false,
        });
      }
    }
  },

  cancelVoiceEdit() {
    this.voiceEditToken += 1;
    if (this.data.voiceEditRecording) {
      stopMemoirVoiceSearchRecording();
    }
    this.setData({
      voiceEditRecording: false,
      voiceEditLoading: false,
      voiceEditSaving: false,
      voiceEditInstructionText: "",
      voiceEditStatusText: "",
      voiceEditPreview: null,
      error: "",
    });
  },

  async confirmVoiceEdit() {
    const item = this.data.item;
    const preview = this.data.voiceEditPreview;
    if (
      !item
      || !preview
      || !preview.canConfirm
      || this.data.voiceEditSaving
      || this.data.saving
      || this.data.deleting
    ) {
      return;
    }
    const content = preview.editedText.trim();
    if (!content) {
      this.setData({ error: "修改后的成书文字不能为空" });
      return;
    }
    this.setData({ voiceEditSaving: true, saving: true, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(item.id, content);
      this.setData({
        item: toViewItem(updated, this.data.bookPlan),
        voiceEditInstructionText: "",
        voiceEditStatusText: "",
        voiceEditPreview: null,
      });
      trackAuditAction("transcript.detail_voice_edit_save", {}, { projectId: updated.project_id });
      wx.showToast({ title: "已保存修改", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存修改失败" });
    } finally {
      this.setData({ voiceEditSaving: false, saving: false });
    }
  },

  async assignTranscriptChapter(event) {
    const item = this.data.item;
    const optionIndex = pickerIndex(event);
    const option = item?.chapterAssignOptions[optionIndex];
    if (!item || !option || this.data.assigning || this.data.moving) {
      return;
    }
    this.stopPlayback();
    this.setData({ assigning: true, error: "" });
    try {
      const updatedPlan = await assignMemoirBookSources(
        item.project_id,
        sourceAssignmentRequest(option.value, item.id),
      );
      this.setData({
        bookPlan: updatedPlan,
        item: toViewItem(await getMemoirVoiceTranscript(item.id), updatedPlan),
      });
      wx.showToast({ title: "已归入章节", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "归入章节失败" });
    } finally {
      this.setData({ assigning: false });
    }
  },

  async moveTranscriptChapter(event) {
    const item = this.data.item;
    const optionIndex = pickerIndex(event);
    const option = item?.chapterMoveOptions[optionIndex];
    if (!item || !option || this.data.assigning || this.data.moving) {
      return;
    }
    this.stopPlayback();
    this.setData({ moving: true, error: "" });
    try {
      const plan = this.data.bookPlan || await loadTranscriptBookPlan(item.project_id);
      const moveRequest = transcriptSourceMoveRequest(item, plan, option.value);
      if (!moveRequest.request) {
        this.setData({ error: moveRequest.error });
        return;
      }
      const updatedPlan = await moveMemoirBookSource(item.project_id, moveRequest.request);
      this.setData({
        bookPlan: updatedPlan,
        item: toViewItem(await getMemoirVoiceTranscript(item.id), updatedPlan),
      });
      wx.showToast({ title: "素材已移动", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "素材移动失败" });
    } finally {
      this.setData({ moving: false });
    }
  },

  async deleteTranscript() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    const confirmed = await confirmDelete(item);
    if (!confirmed) {
      return;
    }
    this.stopPlayback();
    this.setData({ deleting: true, error: "" });
    try {
      await deleteMemoirVoiceTranscript(item.id);
      wx.showToast({ title: "已删除", icon: "success" });
      wx.navigateBack({ delta: 1 });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      this.setData({ deleting: false });
    }
  },

  async playAudio() {
    const item = this.data.item;
    const audioFileIds = [item?.question_audio_file_id, item?.audio_file_id].filter(Boolean) as string[];
    if (!audioFileIds.length) {
      this.setData({ error: "这条记录没有可播放的音频" });
      return;
    }
    if (this.data.playing) {
      this.stopPlayback();
      return;
    }
    if (this.data.saving || this.data.deleting) {
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.setData({ playing: true, error: "" });
    try {
      for (const audioFileId of audioFileIds) {
        if (!this.audioPlayback.isCurrentToken(token)) {
          return;
        }
        await this.audioPlayback.playFile(audioFileId, token);
      }
    } catch (error) {
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({ error: error instanceof Error ? error.message : "播放失败" });
      }
    } finally {
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({ playing: false });
      }
    }
  },

  stopPlayback() {
    this.audioPlayback.stop();
    if (this.data.playing) {
      this.setData({ playing: false });
    }
  },

  goBack() {
    wx.navigateBack({ delta: 1 });
  },

  openLogin() {
    switchToMe();
  },

  onUnload() {
    this.stopPlayback();
    this.cancelVoiceEdit();
  },
});
