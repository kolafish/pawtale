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
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  type PageDatasetEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { readBookTTSRequest } from "../../services/tts-settings";
import { chapterWorkspaceViewPatch } from "./chapter-workspace-view";
import {
  CLOSED_DRAFT_SECTION_EDITOR,
  type ChapterWorkspaceOperationKind,
  type ChapterWorkspacePageData,
} from "./chapter-workspace-model";

interface ChapterWorkspaceDraftHost {
  data: ChapterWorkspacePageData;
  audioPlayback: AudioPlaybackController;
  playToken: number;
  setData(patch: Partial<ChapterWorkspacePageData>): void;
  beginOperation(kind: ChapterWorkspaceOperationKind, targetId?: string): boolean;
  finishOperation(kind: ChapterWorkspaceOperationKind, targetId?: string): void;
  loadPageData(silent?: boolean): Promise<void>;
  syncDraftSectionUnloadAlert(): void;
}

function defineChapterWorkspaceDraftMethods<TMethods extends object>(
  methods: TMethods & ThisType<ChapterWorkspaceDraftHost & TMethods>,
): TMethods {
  return methods;
}

export const chapterWorkspaceDraftMethods = defineChapterWorkspaceDraftMethods({
  openDraftSectionEditor(event: PageDatasetEvent<{ id?: string }>) {
    const workspace = this.data.workspace;
    const sectionId = datasetString(event, "id");
    const section = workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!workspace?.draft_version_id || !section) {
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
      editingMode: "manual",
      editingSectionId: section.section_id,
      editingSectionContent: section.content,
      error: "",
    });
    this.syncDraftSectionUnloadAlert();
  },

  startDraftSectionEdit(event: PageDatasetEvent<{ id?: string }>) {
    this.openDraftSectionEditor(event);
  },

  changeDraftSectionContent(event: PageTextInputEvent) {
    if (this.data.editingMode !== "manual") {
      return;
    }
    this.setData({ editingSectionContent: event.detail.value });
    this.syncDraftSectionUnloadAlert();
  },

  cancelDraftSectionEdit() {
    if (this.data.operationKind) {
      return;
    }
    this.setData(CLOSED_DRAFT_SECTION_EDITOR);
    this.syncDraftSectionUnloadAlert();
  },

  startDraftSectionAIEdit(event: PageDatasetEvent<{ id?: string }>) {
    const workspace = this.data.workspace;
    const sectionId = datasetString(event, "id");
    const section = workspace?.draft_sections.find(
      (item) => item.section_id === sectionId,
    );
    if (!workspace?.draft_version_id || !section) {
      return;
    }
    if (workspace.draft_needs_rewrite) {
      wx.showToast({ title: "请先更新有变化的故事", icon: "none" });
      return;
    }
    if (this.data.operationKind || this.data.editingSectionId) {
      return;
    }
    trackAuditAction(
      "chapter_workspace.section_ai_instruction_edit",
      {},
      { projectId: this.data.projectId },
    );
    wx.navigateTo({
      url: pageUrl("/pages/revision-workbench/index", {
        scope: "chapter_section",
        projectId: this.data.projectId,
        chapterId: this.data.chapterId,
        sectionId,
        sectionTitle: section.title,
      }),
    });
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
      wx.showToast({ title: "故事正文不能为空", icon: "none" });
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
      this.syncDraftSectionUnloadAlert();
      await this.loadPageData(true);
      wx.showToast({ title: "故事已保存", icon: "success" });
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
      "故事朗读失败",
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
