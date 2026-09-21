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
  datasetString,
  pickerIndex,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { promptRequiredText } from "../../services/text-prompt";
import { confirmDeleteTranscript } from "../../services/transcript-view-model";
import {
  changedItem,
  confirmConvertQuestionToAnswer,
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
  voiceEditRequestToken: number;
  setData(patch: Partial<TranscriptDetailData>): void;
  loadTranscript(): Promise<void>;
  stopPlayback(): void;
}

export interface TranscriptDetailEditWorkflowMethods {
  invalidateVoiceEditRequest(): void;
  beginEdit(): void;
  switchEditPreviewVersion(event: PageDatasetEvent<{ version?: string }>): void;
  toggleEditReference(): void;
  toggleRawExpanded(): void;
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
  deleteTranscript(): Promise<void>;
  convertCurrentQuestionToAnswer(): Promise<void>;
}

type TextAreaEvent = PageTextInputEvent;
type MaterialAssignmentPickerEvent = PagePickerEvent;
type TranscriptDetailEditWorkflow = TranscriptDetailEditWorkflowHost
  & TranscriptDetailEditWorkflowMethods;

export const transcriptDetailEditWorkflowMethods:
TranscriptDetailEditWorkflowMethods & ThisType<TranscriptDetailEditWorkflow> = {
  invalidateVoiceEditRequest() {
    if (!this.data.voiceEditLoading) {
      return;
    }
    this.voiceEditRequestToken += 1;
    this.setData({ voiceEditLoading: false });
  },

  beginEdit() {
    const item = this.data.item;
    if (
      !item
      || item.canRecoverTranscription
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
      || this.data.assignmentEditorOpen
    ) {
      return;
    }
    this.invalidateVoiceEditRequest();
    this.stopPlayback();
    const current = item.savedText;
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
      editPreviewVersion: "after",
      voiceEditInstructionText: "",
      voiceEditMessage: "",
      error: "",
    });
    syncEditUnloadAlert(nextItem);
  },

  switchEditPreviewVersion(event) {
    if (!this.data.item?.isEditing) {
      return;
    }
    const version = datasetString(event, "version");
    if (version === "before" || version === "after") {
      this.setData({ editPreviewVersion: version });
    }
  },

  toggleEditReference() {
    if (!this.data.item?.isEditing) {
      return;
    }
    this.setData({ editReferenceExpanded: !this.data.editReferenceExpanded });
  },

  toggleRawExpanded() {
    if (!this.data.item?.rawCanExpand) {
      return;
    }
    this.setData({ rawExpanded: !this.data.rawExpanded });
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
          : "收录位置加载失败",
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
      wx.showToast({ title: "收录位置已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        assignmentError: error instanceof Error
          ? error.message
          : "收录位置更新失败",
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
    this.invalidateVoiceEditRequest();
    const nextItem = changedItem(item, event.detail.value);
    this.setData({
      item: nextItem,
      editPreviewVersion: "after",
      voiceEditMessage: "",
    });
    syncEditUnloadAlert(nextItem);
  },

  async cancelEdit() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    this.invalidateVoiceEditRequest();
    if (item.changed && !await confirmDiscardEdit()) {
      return;
    }
    const nextItem = toViewItem(item);
    this.setData({
      item: nextItem,
      editReferenceExpanded: false,
      editPreviewVersion: "after",
      voiceEditInstructionText: "",
      voiceEditMessage: "",
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
    this.invalidateVoiceEditRequest();
    this.setData({ saving: true, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(item.id, content);
      const nextItem = toViewItem(updated);
      this.setData({
        item: nextItem,
        editReferenceExpanded: false,
        editPreviewVersion: "after",
        voiceEditInstructionText: "",
        voiceEditMessage: "",
      });
      syncEditUnloadAlert(nextItem);
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
    this.invalidateVoiceEditRequest();
    const nextItem = changedItem(item, item.transcript_raw);
    this.setData({
      item: nextItem,
      editPreviewVersion: "after",
      voiceEditMessage: "",
    });
    syncEditUnloadAlert(nextItem);
  },

  changeVoiceEditInstruction(event: PageTextInputEvent) {
    this.setData({
      voiceEditInstructionText: event.detail.value,
      voiceEditMessage: "",
      error: "",
    });
  },

  async previewVoiceEdit(event: PageTextInputEvent) {
    const item = this.data.item;
    const instructionText = event.detail.value.trim();
    if (
      !item
      || item.canRecoverTranscription
      || !item.isEditing
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
      || this.data.voiceEditLoading
    ) {
      return;
    }
    if (!instructionText) {
      wx.showToast({ title: "请先输入或说出修改意见", icon: "none" });
      return;
    }
    const draftText = item.editingText.trim();
    if (!draftText) {
      this.setData({ error: "成书文字不能为空" });
      return;
    }
    this.stopPlayback();
    this.voiceEditRequestToken += 1;
    const requestToken = this.voiceEditRequestToken;
    this.setData({
      voiceEditLoading: true,
      voiceEditInstructionText: instructionText,
      voiceEditMessage: "",
      error: "",
    });
    try {
      const preview = await previewMemoirVoiceTranscriptVoiceEdit(
        item.id,
        instructionText,
        draftText,
      );
      if (requestToken !== this.voiceEditRequestToken) {
        return;
      }
      const currentItem = this.data.item;
      if (!currentItem?.isEditing || currentItem.id !== item.id) {
        return;
      }
      if (currentItem.editingText.trim() !== draftText) {
        this.setData({
          voiceEditMessage: "编辑内容已经变化，刚才生成的结果没有覆盖当前文字。请重新生成。",
        });
        return;
      }
      trackAuditAction("transcript.detail_voice_edit_preview", { action: preview.action }, { projectId: item.project_id });
      if (
        preview.action !== "revise_transcript"
        || !preview.edited_text.trim()
        || preview.edited_text.trim() === draftText
      ) {
        this.setData({ voiceEditMessage: preview.reply_text });
        return;
      }
      const nextItem = changedItem(currentItem, preview.edited_text);
      this.setData({
        item: nextItem,
        editPreviewVersion: "after",
        voiceEditInstructionText: "",
        voiceEditMessage: "已生成修改后文字，可与修改前对比后统一保存。",
      });
      syncEditUnloadAlert(nextItem);
    } catch (error) {
      if (
        requestToken === this.voiceEditRequestToken
        && this.data.item?.isEditing
        && this.data.item.id === item.id
      ) {
        const message = error instanceof Error ? error.message : "语音修改失败";
        this.setData({ error: message });
      }
    } finally {
      if (requestToken === this.voiceEditRequestToken) {
        this.setData({ voiceEditLoading: false });
      }
    }
  },

  async deleteTranscript() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting || this.data.convertingQuestionToAnswer) {
      return;
    }
    const confirmed = await confirmDeleteTranscript(item.titleText || item.display_text || item.transcript_raw);
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
