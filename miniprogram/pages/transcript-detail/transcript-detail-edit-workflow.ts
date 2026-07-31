import { trackAuditAction } from "../../services/audit-tracker";
import {
  convertMemoirInterviewQuestionToAnswer,
  deleteMemoirVoiceTranscript,
  getMemoirVoiceTranscript,
  previewMemoirVoiceTranscriptVoiceEdit,
  recoverMemoirVoiceTranscript,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-transcript-api";
import { getMemoirBookPlan } from "../../services/memoir-book-api";
import { placeMemoirChapterMaterial } from "../../services/memoir-chapter-workspace-api";
import { routeAuthError } from "../../services/navigation";
import {
  pickerIndex,
  type PagePickerEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { promptRequiredText } from "../../services/text-prompt";
import { transcriptVoiceEditPreviewView } from "../../services/transcript-view-model";
import {
  changedItem,
  confirmConvertQuestionToAnswer,
  confirmDelete,
  confirmDiscardEdit,
  materialAssignmentOptions,
  materialAssignmentPatch,
  syncEditUnloadAlert,
  toViewItem,
  type TranscriptDetailData,
  type TranscriptDetailItemView,
} from "./transcript-detail-model";

interface TranscriptDetailEditWorkflowHost {
  data: TranscriptDetailData;
  setData(patch: Partial<TranscriptDetailData>): void;
  loadTranscript(): Promise<void>;
  resetShareVideoState(): void;
  stopPlayback(): void;
}

export interface TranscriptDetailEditWorkflowMethods {
  beginEdit(): void;
  toggleEditReference(): void;
  toggleQuestionExpanded(): void;
  toggleMoreActionsExpanded(): void;
  toggleMaterialAssignmentEditor(): Promise<void>;
  closeMaterialAssignmentEditor(): void;
  changeMaterialAssignment(event: PagePickerEvent): Promise<void>;
  saveMaterialAssignment(): Promise<void>;
  changeText(event: PageTextInputEvent): void;
  cancelEdit(): Promise<void>;
  saveTranscript(): Promise<void>;
  recoverTranscript(): Promise<void>;
  resetToRaw(): void;
  changeVoiceEditInstruction(event: PageTextInputEvent): void;
  previewVoiceEdit(event: PageTextInputEvent): Promise<void>;
  cancelVoiceEdit(): void;
  confirmVoiceEdit(): Promise<void>;
  deleteTranscript(): Promise<void>;
  convertCurrentQuestionToAnswer(): Promise<void>;
}

type TextAreaEvent = PageTextInputEvent;
type MaterialAssignmentPickerEvent = PagePickerEvent;
type TranscriptDetailEditWorkflow = TranscriptDetailEditWorkflowHost
  & TranscriptDetailEditWorkflowMethods;

export const transcriptDetailEditWorkflowMethods:
TranscriptDetailEditWorkflowMethods & ThisType<TranscriptDetailEditWorkflow> = {
  beginEdit() {
    const item = this.data.item;
    if (
      !item
      || item.canRecoverTranscription
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
      || this.data.voiceEditLoading
      || this.data.voiceEditSaving
      || this.data.assignmentEditorOpen
    ) {
      return;
    }
    this.stopPlayback();
    this.resetShareVideoState();
    const current = item.transcript_edited || item.transcript_raw;
    const nextItem: TranscriptDetailItemView = {
      ...item,
      editingText: current,
      editingCharCount: current.trim().length,
      changed: false,
      isEditing: true,
    };
    this.setData({
      item: nextItem,
      editReferenceExpanded: false,
      error: "",
    });
    syncEditUnloadAlert(nextItem);
  },

  toggleEditReference() {
    if (!this.data.item?.isEditing) {
      return;
    }
    this.setData({ editReferenceExpanded: !this.data.editReferenceExpanded });
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

  async toggleMaterialAssignmentEditor() {
    const item = this.data.item;
    if (
      !item
      || item.canRecoverTranscription
      || item.isEditing
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    if (this.data.assignmentEditorOpen) {
      this.closeMaterialAssignmentEditor();
      return;
    }
    this.setData({
      assignmentEditorOpen: true,
      assignmentLoading: true,
      assignmentError: "",
    });
    try {
      const plan = await getMemoirBookPlan(item.project_id);
      const options = materialAssignmentOptions(plan);
      this.setData(materialAssignmentPatch(options, item));
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        assignmentError: error instanceof Error
          ? error.message
          : "章节和小节加载失败",
      });
    } finally {
      this.setData({ assignmentLoading: false });
    }
  },

  closeMaterialAssignmentEditor() {
    if (this.data.assignmentSaving) {
      return;
    }
    this.setData({
      assignmentEditorOpen: false,
      assignmentError: "",
      assignmentPlacement: null,
    });
  },

  async changeMaterialAssignment(event: MaterialAssignmentPickerEvent) {
    const item = this.data.item;
    if (!item || this.data.assignmentLoading || this.data.assignmentSaving) {
      return;
    }
    const assignmentIndex = pickerIndex(event);
    const target = this.data.assignmentOptions[assignmentIndex];
    if (!target) {
      return;
    }
    if (target.kind === "existing_section") {
      this.setData(materialAssignmentPatch(
        this.data.assignmentOptions,
        item,
        assignmentIndex,
      ));
      return;
    }
    if (target.kind === "create_section" && target.chapterId) {
      const sectionTitle = await promptRequiredText(
        "新建小节",
        "请输入小节名称",
      );
      if (!sectionTitle) {
        return;
      }
      this.setData({
        assignmentIndex,
        assignmentText: `${target.label.replace("＋ 新建小节", "")}新建小节：${sectionTitle}`,
        assignmentCanSave: true,
        assignmentPlacement: {
          target_chapter_id: target.chapterId,
          new_section_title: sectionTitle,
        },
      });
      return;
    }
    const chapterTitle = await promptRequiredText(
      "新建章节",
      "请输入章节名称",
    );
    if (!chapterTitle) {
      return;
    }
    const sectionTitle = await promptRequiredText(
      "新建第一个小节",
      "请输入小节名称",
    );
    if (!sectionTitle) {
      return;
    }
    this.setData({
      assignmentIndex,
      assignmentText: `新建章节：${chapterTitle} · 新建小节：${sectionTitle}`,
      assignmentCanSave: true,
      assignmentPlacement: {
        new_chapter_title: chapterTitle,
        new_section_title: sectionTitle,
      },
    });
  },

  async saveMaterialAssignment() {
    const item = this.data.item;
    const placement = this.data.assignmentPlacement;
    if (
      !item
      || !placement
      || !this.data.assignmentCanSave
      || this.data.assignmentLoading
      || this.data.assignmentSaving
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    this.stopPlayback();
    this.setData({
      saving: true,
      assignmentSaving: true,
      assignmentError: "",
    });
    try {
      await placeMemoirChapterMaterial(
        item.project_id,
        item.id,
        placement,
      );
      const updated = await getMemoirVoiceTranscript(item.id);
      this.setData({
        item: toViewItem(updated),
        assignmentEditorOpen: false,
      });
      trackAuditAction(
        "transcript.detail_material_assignment",
        {
          chapter_id: updated.section_assignment?.chapter_id || "",
          section_id: updated.section_assignment?.section_id || "",
        },
        { projectId: item.project_id },
      );
      wx.showToast({ title: "章节和小节已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        assignmentError: error instanceof Error
          ? error.message
          : "章节和小节更新失败",
      });
    } finally {
      this.setData({
        saving: false,
        assignmentSaving: false,
      });
    }
  },

  changeText(event: TextAreaEvent) {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription) {
      return;
    }
    const nextItem = changedItem(item, event.detail.value);
    this.setData({ item: nextItem });
    syncEditUnloadAlert(nextItem);
  },

  async cancelEdit() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    if (item.changed && !await confirmDiscardEdit()) {
      return;
    }
    const nextItem = toViewItem(item);
    this.setData({
      item: nextItem,
      editReferenceExpanded: false,
      error: "",
    });
    syncEditUnloadAlert(nextItem);
  },

  async saveTranscript() {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription || this.data.saving || this.data.deleting || this.data.convertingQuestionToAnswer) {
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
      const nextItem = toViewItem(updated);
      this.setData({
        item: nextItem,
        editReferenceExpanded: false,
      });
      syncEditUnloadAlert(nextItem);
      this.resetShareVideoState();
      trackAuditAction("transcript.detail_edit_save", {}, { projectId: updated.project_id });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
    } finally {
      this.setData({ saving: false });
    }
  },

  async recoverTranscript() {
    const item = this.data.item;
    if (
      !item ||
      !item.canRecoverTranscription ||
      this.data.recovering ||
      this.data.saving ||
      this.data.deleting ||
      this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    this.setData({ recovering: true, error: "" });
    try {
      const recovered = await recoverMemoirVoiceTranscript(item.id);
      this.setData({ item: toViewItem(recovered) });
      this.resetShareVideoState();
      trackAuditAction("transcript.detail_recover_transcription", {}, { projectId: recovered.project_id });
      wx.showToast({ title: "已恢复转写", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "恢复转写失败" });
    } finally {
      this.setData({ recovering: false });
    }
  },

  resetToRaw() {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription) {
      return;
    }
    const nextItem = changedItem(item, item.transcript_raw);
    this.setData({ item: nextItem });
    syncEditUnloadAlert(nextItem);
  },

  changeVoiceEditInstruction(event: PageTextInputEvent) {
    this.setData({
      voiceEditInstructionText: event.detail.value,
      voiceEditPreview: null,
      error: "",
    });
  },

  async previewVoiceEdit(event: PageTextInputEvent) {
    const item = this.data.item;
    const instructionText = event.detail.value.trim();
    if (
      !item
      || item.canRecoverTranscription
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
      || this.data.voiceEditLoading
      || this.data.voiceEditSaving
    ) {
      return;
    }
    if (!instructionText) {
      wx.showToast({ title: "请先输入或说出修改意见", icon: "none" });
      return;
    }
    this.stopPlayback();
    this.setData({
      voiceEditLoading: true,
      voiceEditSaving: false,
      voiceEditInstructionText: instructionText,
      voiceEditPreview: null,
      error: "",
    });
    try {
      const preview = await previewMemoirVoiceTranscriptVoiceEdit(item.id, instructionText);
      this.setData({
        voiceEditPreview: transcriptVoiceEditPreviewView(preview),
        error: preview.action === "revise_transcript" ? "" : preview.reply_text,
      });
      trackAuditAction("transcript.detail_voice_edit_preview", { action: preview.action }, { projectId: item.project_id });
    } catch (error) {
      const message = error instanceof Error ? error.message : "语音修改失败";
      this.setData({ error: message });
    } finally {
      this.setData({ voiceEditLoading: false });
    }
  },

  cancelVoiceEdit() {
    this.setData({
      voiceEditLoading: false,
      voiceEditSaving: false,
      voiceEditInstructionText: "",
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
      || this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    const content = preview.editedText.trim();
    if (!content) {
      const message = "修改后的成书文字不能为空";
      this.setData({ error: message });
      return;
    }
    this.setData({
      voiceEditSaving: true,
      saving: true,
      error: "",
    });
    try {
      const updated = await updateMemoirVoiceTranscript(item.id, content);
      this.setData({
        item: toViewItem(updated),
        voiceEditInstructionText: "",
        voiceEditPreview: null,
      });
      this.resetShareVideoState();
      trackAuditAction("transcript.detail_voice_edit_save", {}, { projectId: updated.project_id });
      wx.showToast({ title: "已保存修改", icon: "success" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存修改失败";
      this.setData({ error: message });
    } finally {
      this.setData({ voiceEditSaving: false, saving: false });
    }
  },

  async deleteTranscript() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting || this.data.convertingQuestionToAnswer) {
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

  async convertCurrentQuestionToAnswer() {
    const item = this.data.item;
    if (
      !item ||
      !item.canConvertQuestionToAnswer ||
      !item.chat_message_id ||
      this.data.saving ||
      this.data.deleting ||
      this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    const confirmed = await confirmConvertQuestionToAnswer(item);
    if (!confirmed) {
      return;
    }
    this.stopPlayback();
    this.resetShareVideoState();
    this.setData({ convertingQuestionToAnswer: true, error: "" });
    try {
      await convertMemoirInterviewQuestionToAnswer(item.project_id, item.chat_message_id);
      wx.showToast({ title: "已改成回答", icon: "success" });
      await this.loadTranscript();
      this.setData({ moreActionsExpanded: false });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "转换失败，请稍后重试" });
    } finally {
      this.setData({ convertingQuestionToAnswer: false });
    }
  },
};
