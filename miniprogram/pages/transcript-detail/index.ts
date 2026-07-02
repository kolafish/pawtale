import type {
  MemoirBookPlan,
  MemoirVoiceTranscriptItem,
  MemoirVoiceTranscriptVoiceEditPreviewResponse,
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
import { formatBeijingDateTimeShort } from "../../services/time";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";

interface TextAreaEvent {
  detail: {
    value: string;
  };
}

interface PickerEvent {
  detail: {
    value: string | number;
  };
}

interface TranscriptTextSegment {
  text: string;
  highlight: boolean;
}

type TranscriptVoiceEditDiffKind = "equal" | "delete" | "insert";

interface TranscriptVoiceEditChangeView {
  before: string;
  after: string;
  reason: string;
}

interface TranscriptVoiceEditDiffSegment {
  text: string;
  kind: TranscriptVoiceEditDiffKind;
}

interface TranscriptVoiceEditPreviewView {
  action: MemoirVoiceTranscriptVoiceEditPreviewResponse["action"];
  currentText: string;
  editedText: string;
  replyText: string;
  confidenceText: string;
  changes: TranscriptVoiceEditChangeView[];
  diffSegments: TranscriptVoiceEditDiffSegment[];
  canConfirm: boolean;
}

interface TranscriptDetailItemView extends MemoirVoiceTranscriptItem {
  titleText: string;
  editingText: string;
  editingCharCount: number;
  createdAtText: string;
  audioText: string;
  bookInclusionText: string;
  isIncludedInBook: boolean;
  canAssignChapterSource: boolean;
  chapterAssignOptions: ChapterPickerOption[];
  canMoveChapterSource: boolean;
  chapterMoveOptions: ChapterPickerOption[];
  canPlayAudio: boolean;
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
  voiceEditRecording: boolean;
  voiceEditLoading: boolean;
  voiceEditSaving: boolean;
  voiceEditInstructionText: string;
  voiceEditStatusText: string;
  voiceEditPreview: TranscriptVoiceEditPreviewView | null;
}

const MAX_INLINE_DIFF_PRODUCT = 400000;

function formatDurationSeconds(value: number): string {
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分`;
}

function formatDuration(item: MemoirVoiceTranscriptItem): string {
  if (item.audio_duration_seconds !== null && item.audio_duration_seconds !== undefined) {
    return formatDurationSeconds(item.audio_duration_seconds);
  }
  if (item.start_ms !== null && item.start_ms !== undefined && item.end_ms !== null && item.end_ms !== undefined) {
    return formatDurationSeconds(Math.max(0, Math.round((item.end_ms - item.start_ms) / 1000)));
  }
  return "";
}

function transcriptAudioText(item: MemoirVoiceTranscriptItem): string {
  const duration = formatDuration(item);
  return duration ? `时长 ${duration}` : "时长未知";
}

function transcriptCreatedAtText(item: Pick<MemoirVoiceTranscriptItem, "created_at">): string {
  return formatBeijingDateTimeShort(item.created_at) || "讲述记录";
}

function plainSegments(text: string): TranscriptTextSegment[] {
  return text ? [{ text, highlight: false }] : [];
}

function transcriptTitleText(item: MemoirVoiceTranscriptItem): string {
  const summaryTitle = (item.summary_title || "").trim();
  if (summaryTitle) {
    return summaryTitle;
  }
  const displayText = (item.display_text || item.transcript_edited || item.transcript_raw || "").trim();
  return displayText ? displayText.slice(0, 32) : "讲述记录";
}

function transcriptBookInclusionText(item: MemoirVoiceTranscriptItem): string {
  const chapters = [...(item.included_chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => chapter.title.trim())
    .filter(Boolean);
  if (!chapters.length) {
    return "未收录到章节";
  }
  const visible = chapters.slice(0, 2).join("、");
  const suffix = chapters.length > 2 ? "等章节" : "";
  return `已收录：${visible}${suffix}`;
}

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
  return {
    ...item,
    titleText: transcriptTitleText(item),
    editingText: previous?.isEditing ? previous.editingText : editingText,
    editingCharCount: (previous?.isEditing ? previous.editingText : editingText).trim().length,
    createdAtText: transcriptCreatedAtText(item),
    audioText: transcriptAudioText(item),
    bookInclusionText: transcriptBookInclusionText(item),
    isIncludedInBook,
    canAssignChapterSource: chapterAssignOptions.length > 0,
    chapterAssignOptions,
    canMoveChapterSource: chapterMoveOptions.length > 0,
    chapterMoveOptions,
    canPlayAudio: Boolean(item.audio_file_id || item.question_audio_file_id),
    questionSegments: plainSegments(item.question_text),
    rawSegments: plainSegments(item.transcript_raw),
    displaySegments: plainSegments(item.display_text),
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

function pushDiffSegment(
  segments: TranscriptVoiceEditDiffSegment[],
  kind: TranscriptVoiceEditDiffKind,
  text: string,
) {
  if (!text) {
    return;
  }
  const last = segments[segments.length - 1];
  if (last?.kind === kind) {
    last.text += text;
    return;
  }
  segments.push({ kind, text });
}

function editedTextFromDiffSegments(segments: TranscriptVoiceEditDiffSegment[]): string {
  return segments
    .filter((segment) => segment.kind !== "delete")
    .map((segment) => segment.text)
    .join("");
}

function diffSegmentsFromChangeList(
  currentText: string,
  editedText: string,
  changes: TranscriptVoiceEditChangeView[],
): TranscriptVoiceEditDiffSegment[] | null {
  if (!changes.length) {
    return null;
  }
  const segments: TranscriptVoiceEditDiffSegment[] = [];
  let cursor = 0;
  for (const change of changes) {
    const before = change.before || "";
    const after = change.after || "";
    if (!before && !after) {
      continue;
    }
    if (!before) {
      return null;
    }
    const index = currentText.indexOf(before, cursor);
    if (index < 0) {
      return null;
    }
    pushDiffSegment(segments, "equal", currentText.slice(cursor, index));
    pushDiffSegment(segments, "delete", before);
    pushDiffSegment(segments, "insert", after);
    cursor = index + before.length;
  }
  pushDiffSegment(segments, "equal", currentText.slice(cursor));
  return editedTextFromDiffSegments(segments) === editedText ? segments : null;
}

function textDiffSegments(currentText: string, editedText: string): TranscriptVoiceEditDiffSegment[] {
  const left = Array.from(currentText);
  const right = Array.from(editedText);
  const segments: TranscriptVoiceEditDiffSegment[] = [];
  let prefixLength = 0;
  while (
    prefixLength < left.length
    && prefixLength < right.length
    && left[prefixLength] === right[prefixLength]
  ) {
    prefixLength += 1;
  }
  let suffixLength = 0;
  while (
    suffixLength < left.length - prefixLength
    && suffixLength < right.length - prefixLength
    && left[left.length - suffixLength - 1] === right[right.length - suffixLength - 1]
  ) {
    suffixLength += 1;
  }
  pushDiffSegment(segments, "equal", left.slice(0, prefixLength).join(""));
  const leftMiddle = left.slice(prefixLength, left.length - suffixLength);
  const rightMiddle = right.slice(prefixLength, right.length - suffixLength);
  if (!leftMiddle.length) {
    pushDiffSegment(segments, "insert", rightMiddle.join(""));
  } else if (!rightMiddle.length) {
    pushDiffSegment(segments, "delete", leftMiddle.join(""));
  } else if (leftMiddle.length * rightMiddle.length > MAX_INLINE_DIFF_PRODUCT) {
    pushDiffSegment(segments, "delete", leftMiddle.join(""));
    pushDiffSegment(segments, "insert", rightMiddle.join(""));
  } else {
    const width = rightMiddle.length + 1;
    const table = new Uint32Array((leftMiddle.length + 1) * width);
    for (let leftIndex = leftMiddle.length - 1; leftIndex >= 0; leftIndex -= 1) {
      for (let rightIndex = rightMiddle.length - 1; rightIndex >= 0; rightIndex -= 1) {
        const current = leftIndex * width + rightIndex;
        table[current] = leftMiddle[leftIndex] === rightMiddle[rightIndex]
          ? table[(leftIndex + 1) * width + rightIndex + 1] + 1
          : Math.max(table[(leftIndex + 1) * width + rightIndex], table[leftIndex * width + rightIndex + 1]);
      }
    }
    let leftIndex = 0;
    let rightIndex = 0;
    while (leftIndex < leftMiddle.length && rightIndex < rightMiddle.length) {
      if (leftMiddle[leftIndex] === rightMiddle[rightIndex]) {
        pushDiffSegment(segments, "equal", leftMiddle[leftIndex]);
        leftIndex += 1;
        rightIndex += 1;
      } else if (
        table[(leftIndex + 1) * width + rightIndex] >= table[leftIndex * width + rightIndex + 1]
      ) {
        pushDiffSegment(segments, "delete", leftMiddle[leftIndex]);
        leftIndex += 1;
      } else {
        pushDiffSegment(segments, "insert", rightMiddle[rightIndex]);
        rightIndex += 1;
      }
    }
    pushDiffSegment(segments, "delete", leftMiddle.slice(leftIndex).join(""));
    pushDiffSegment(segments, "insert", rightMiddle.slice(rightIndex).join(""));
  }
  pushDiffSegment(segments, "equal", suffixLength ? left.slice(left.length - suffixLength).join("") : "");
  return segments;
}

function inlineTranscriptVoiceEditDiffSegments(
  currentText: string,
  editedText: string,
  changes: TranscriptVoiceEditChangeView[] = [],
): TranscriptVoiceEditDiffSegment[] {
  if (currentText === editedText) {
    return currentText ? [{ text: currentText, kind: "equal" }] : [];
  }
  const changeListSegments = diffSegmentsFromChangeList(currentText, editedText, changes);
  return changeListSegments || textDiffSegments(currentText, editedText);
}

function voiceEditPreviewView(
  response: MemoirVoiceTranscriptVoiceEditPreviewResponse,
): TranscriptVoiceEditPreviewView {
  const confidence = Number.isFinite(response.confidence)
    ? Math.round(Math.max(0, Math.min(1, response.confidence)) * 100)
    : 0;
  const changes = (response.changes || []).map((item) => ({
    before: item.before,
    after: item.after,
    reason: item.reason,
  }));
  return {
    action: response.action,
    currentText: response.current_text,
    editedText: response.edited_text,
    replyText: response.reply_text,
    confidenceText: `${confidence}%`,
    changes,
    diffSegments: inlineTranscriptVoiceEditDiffSegments(response.current_text, response.edited_text, changes),
    canConfirm: Boolean(response.action === "revise_transcript"
      && response.edited_text.trim()
      && response.edited_text.trim() !== response.current_text.trim()),
  };
}

function confirmDelete(item: TranscriptDetailItemView): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = item.titleText || item.display_text || item.transcript_raw || "这条讲述记录";
    wx.showModal({
      title: "删除讲述记录",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后不会再出现在讲述记录和访谈上下文中。`,
      confirmText: "删除",
      confirmColor: "#b4234a",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

Page<TranscriptDetailData, {
  onLoad(options?: Record<string, string | undefined>): Promise<void>;
  loadTranscript(): Promise<void>;
  beginEdit(): void;
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
    voiceEditRecording: false,
    voiceEditLoading: false,
    voiceEditSaving: false,
    voiceEditInstructionText: "",
    voiceEditStatusText: "",
    voiceEditPreview: null,
  },

  async onLoad(options) {
    const utteranceId = options?.id ? decodeURIComponent(options.id) : "";
    this.setData({ utteranceId });
    await this.loadTranscript();
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
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
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
      this.setData({ error: "整理文字不能为空" });
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
      voiceEditStatusText: "正在听你说要改哪里",
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
            voiceEditStatusText: "正在识别修正内容",
          });
        },
        emptyTranscriptMessage: "没有识别到修正内容，请再说一次",
        noVoiceTranscriptMessage: "没有听到修正内容，请再说一次",
      });
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        voiceEditInstructionText: instructionText,
        voiceEditStatusText: "正在生成修正预览",
      });
      const preview = await previewMemoirVoiceTranscriptVoiceEdit(item.id, instructionText);
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        voiceEditPreview: voiceEditPreviewView(preview),
        voiceEditStatusText: preview.action === "revise_transcript" ? "请确认修正内容" : preview.reply_text,
      });
      trackAuditAction("transcript.detail_voice_edit_preview", { action: preview.action }, { projectId: item.project_id });
    } catch (error) {
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音修正失败",
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
      this.setData({ error: "修正后的整理文字不能为空" });
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
      wx.showToast({ title: "已保存修正", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存修正失败" });
    } finally {
      this.setData({ voiceEditSaving: false, saving: false });
    }
  },

  async assignTranscriptChapter(event) {
    const item = this.data.item;
    const optionIndex = Number(event.detail.value);
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
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "归入章节失败" });
    } finally {
      this.setData({ assigning: false });
    }
  },

  async moveTranscriptChapter(event) {
    const item = this.data.item;
    const optionIndex = Number(event.detail.value);
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
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.switchTab({ url: "/pages/me/index" });
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
    wx.switchTab({ url: "/pages/me/index" });
  },

  onUnload() {
    this.stopPlayback();
    this.cancelVoiceEdit();
  },
});
