import type { MemoirBookAudioResponse } from "../../domain/memoir-book";
import {
  AudioPlaybackController,
  playAudioFileSequence,
} from "../../services/audio-playback";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  confirmMemoirBookChapterDraft,
  createMemoirBookChapterDraftSectionTTS,
  reviseMemoirBookChapterDraft,
} from "../../services/memoir-book-api";
import { reviseMemoirChapterDraftSectionWithAI } from "../../services/memoir-chapter-workspace-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  type PageDatasetEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { readBookTTSRequest } from "../../services/tts-settings";
import { chapterWorkspaceViewPatch } from "./chapter-workspace-view";
import {
  CLOSED_DRAFT_SECTION_EDITOR,
  type ChapterWorkspaceOperationKind,
  type ChapterWorkspacePageData,
  type DraftSectionEditingMode,
} from "./chapter-workspace-model";

interface ChapterWorkspaceDraftHost {
  data: ChapterWorkspacePageData;
  audioPlayback: AudioPlaybackController;
  playToken: number;
  setData(patch: Partial<ChapterWorkspacePageData>): void;
  beginOperation(kind: ChapterWorkspaceOperationKind, targetId?: string): boolean;
  finishOperation(kind: ChapterWorkspaceOperationKind, targetId?: string): void;
  loadPageData(silent?: boolean): Promise<void>;
}

function defineChapterWorkspaceDraftMethods<TMethods extends object>(
  methods: TMethods & ThisType<ChapterWorkspaceDraftHost & TMethods>,
): TMethods {
  return methods;
}

export const chapterWorkspaceDraftMethods = defineChapterWorkspaceDraftMethods({
  openDraftSectionEditor(
    event: PageDatasetEvent<{ id?: string }>,
    editingMode: Exclude<DraftSectionEditingMode, "">,
  ) {
    const workspace = this.data.workspace;
    const sectionId = datasetString(event, "id");
    const section = workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!workspace?.draft_version_id || !section) {
      return;
    }
    if (editingMode === "ai" && workspace.draft_needs_rewrite) {
      wx.showToast({
        title: "请先更新有变化的小节正文",
        icon: "none",
      });
      return;
    }
    if (
      this.data.operationKind
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    const expandedSectionIds = {
      ...this.data.expandedSectionIds,
      [section.section_id]: true,
    };
    this.setData({
      expandedSectionIds,
      ...chapterWorkspaceViewPatch(
        workspace,
        expandedSectionIds,
      ),
      editingMode,
      editingSectionId: section.section_id,
      editingSectionContent: section.content,
      draftSectionAIInstruction: "",
      error: "",
    });
  },

  startDraftSectionEdit(event: PageDatasetEvent<{ id?: string }>) {
    this.openDraftSectionEditor(event, "manual");
  },

  changeDraftSectionContent(event: PageTextInputEvent) {
    if (this.data.editingMode !== "manual") {
      return;
    }
    this.setData({ editingSectionContent: event.detail.value });
  },

  cancelDraftSectionEdit() {
    if (this.data.operationKind) {
      return;
    }
    this.setData(CLOSED_DRAFT_SECTION_EDITOR);
  },

  startDraftSectionAIEdit(event: PageDatasetEvent<{ id?: string }>) {
    this.openDraftSectionEditor(event, "ai");
  },

  changeDraftSectionAIInstruction(event: PageTextInputEvent) {
    if (this.data.editingMode !== "ai") {
      return;
    }
    this.setData({ draftSectionAIInstruction: event.detail.value });
  },

  async submitDraftSectionAIInstruction(event: PageTextInputEvent) {
    const sectionId = this.data.editingSectionId;
    const instructionText = event.detail.value.trim();
    if (
      this.data.editingMode !== "ai"
      || !sectionId
      || this.data.operationKind
    ) {
      return;
    }
    if (!instructionText) {
      wx.showToast({ title: "请先说出或输入修改要求", icon: "none" });
      return;
    }
    this.setData({ error: "" });
    await this.performDraftSectionAIRevision(
      sectionId,
      instructionText,
    );
  },

  async saveDraftSectionEdit() {
    const workspace = this.data.workspace;
    const sectionId = this.data.editingSectionId;
    const content = this.data.editingSectionContent.trim();
    const currentSection = workspace?.draft_sections.find(
      (section) => section.section_id === sectionId,
    );
    if (
      !workspace?.draft_version_id
      || !currentSection
      || this.data.editingMode !== "manual"
      || Boolean(this.data.operationKind)
    ) {
      return;
    }
    if (!content) {
      wx.showToast({ title: "小节正文不能为空", icon: "none" });
      return;
    }
    if (content === currentSection.content.trim()) {
      this.cancelDraftSectionEdit();
      return;
    }
    if (!this.beginOperation("save_draft_section", sectionId)) {
      return;
    }
    try {
      await reviseMemoirBookChapterDraft(
        this.data.projectId,
        this.data.chapterId,
        {
          expected_version_id: workspace.draft_version_id,
          sections: workspace.draft_sections.map((section) => ({
            section_id: section.section_id,
            content: section.section_id === sectionId
              ? content
              : section.content,
          })),
        },
      );
      this.setData(CLOSED_DRAFT_SECTION_EDITOR);
      await this.loadPageData(true);
      wx.showToast({ title: "小节已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "正文保存失败" });
    } finally {
      this.finishOperation("save_draft_section", sectionId);
    }
  },

  async confirmChapter() {
    if (
      !this.data.canConfirmChapter
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    if (!this.beginOperation("confirm_chapter")) {
      return;
    }
    try {
      await confirmMemoirBookChapterDraft(this.data.projectId, this.data.chapterId);
      await this.loadPageData(true);
      wx.showToast({ title: "本章正文已确认", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "本章正文确认失败" });
    } finally {
      this.finishOperation("confirm_chapter");
    }
  },

  async performDraftSectionAIRevision(
    sectionId: string,
    instructionText: string,
  ) {
    const workspace = this.data.workspace;
    const normalizedInstruction = instructionText.trim();
    if (
      !workspace?.draft_version_id
      || !sectionId
      || !normalizedInstruction
      || !this.beginOperation("revise_draft_section_with_ai", sectionId)
    ) {
      return;
    }
    try {
      const revisedWorkspace = await reviseMemoirChapterDraftSectionWithAI(
        this.data.projectId,
        this.data.chapterId,
        sectionId,
        workspace.draft_version_id,
        normalizedInstruction,
      );
      if (
        revisedWorkspace.draft_version_id === workspace.draft_version_id
      ) {
        this.setData({
          error: "AI 没有改动这一小节，请把修改要求说得更具体一些",
        });
        return;
      }
      const expandedSectionIds = {
        ...this.data.expandedSectionIds,
        [sectionId]: true,
      };
      this.setData({
        expandedSectionIds,
        ...CLOSED_DRAFT_SECTION_EDITOR,
      });
      await this.loadPageData(true);
      wx.showToast({
        title: "已按要求修改",
        icon: "success",
      });
      trackAuditAction(
        "chapter_workspace.section_ai_instruction_edit",
        {},
        { projectId: this.data.projectId },
      );
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error
          ? error.message
          : "小节 AI 修改失败",
      });
    } finally {
      this.finishOperation("revise_draft_section_with_ai", sectionId);
    }
  },

  async playDraftSection(event: PageDatasetEvent<{ id?: string }>) {
    const sectionId = datasetString(event, "id");
    const section = this.data.workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!section || !this.data.projectId || !this.data.chapterId) {
      return;
    }
    await this.playDraftAudio(
      sectionId,
      () => createMemoirBookChapterDraftSectionTTS(
        this.data.projectId,
        this.data.chapterId,
        sectionId,
        readBookTTSRequest(),
      ),
      "小节朗读失败",
    );
  },

  async playDraftAudio(
    target: string,
    createAudio: () => Promise<MemoirBookAudioResponse>,
    errorMessage: string,
  ) {
    if (
      this.data.audioTarget === target
      && this.data.audioPhase !== "idle"
    ) {
      this.stopDraftSectionPlayback();
      return;
    }
    if (
      this.data.audioPhase !== "idle"
      || Boolean(this.data.operationKind)
      || Boolean(this.data.editingSectionId)
    ) {
      return;
    }
    this.stopDraftSectionPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.setData({
      audioPhase: "preparing",
      audioTarget: target,
      error: "",
    });
    try {
      const response = await createAudio();
      if (token !== this.playToken) {
        return;
      }
      this.setData({
        audioPhase: "playing",
        audioTarget: target,
      });
      await playAudioFileSequence(response.audio_file_ids || [], {
        token,
        isCurrent: (candidate) => candidate === this.playToken,
        playFile: (audioFileId) => this.audioPlayback.playFile(
          audioFileId,
          token,
          { errorMessage },
        ),
      });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (token === this.playToken) {
        this.setData({
          error: error instanceof Error ? error.message : errorMessage,
        });
      }
    } finally {
      if (token === this.playToken) {
        this.setData({
          audioPhase: "idle",
          audioTarget: "",
        });
      }
    }
  },

  stopDraftSectionPlayback() {
    this.audioPlayback.stop();
    this.playToken = this.audioPlayback.currentToken();
    if (this.data.audioPhase !== "idle" || this.data.audioTarget) {
      this.setData({
        audioPhase: "idle",
        audioTarget: "",
      });
    }
  },
});
