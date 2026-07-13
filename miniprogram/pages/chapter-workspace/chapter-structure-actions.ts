import type {
  MemoirChapterContractRevisionRequest,
  MemoirChapterStructureMergeRequest,
  MemoirChapterStructureMutationResponse,
  MemoirChapterStructureSplitRequest,
  MemoirChapterWorkspace,
  MemoirChapterWorkspaceContract,
  MemoirChapterWorkspaceNode,
} from "../../domain/chapter-workspace";
import type { MemoirBookPlan } from "../../domain/voice-chat";
import { routeAuthError } from "../../services/navigation";
import { pageUrl, type PageTextInputEvent } from "../../services/page-input";
import {
  buildContractRevisionRequest,
  buildSplitRequest,
  chapterStructureIntentKey,
  chapterMergeOptions,
  contractEditorFields,
  splitEditorFields,
  type ChapterMergeOption,
} from "./chapter-structure-editor";


type ConfirmDialog = (
  title: string,
  content: string,
  confirmText: string,
) => Promise<boolean>;

interface StructureNodeView extends MemoirChapterWorkspaceNode {
  selectedForSplit: boolean;
}

interface ChapterStructureState {
  projectId: string;
  chapterId: string;
  workspace: MemoirChapterWorkspace | null;
  error: string;
  contractActionBusy: boolean;
  contractActionStatus: string;
  contractEditorOpen: boolean;
  contractObjective: string;
  contractReaderTakeaway: string;
  contractTimeScopeText: string;
  contractQuestionsText: string;
  structureActionBusy: boolean;
  topologyActionStatus: string;
  topologyError: string;
  splitEditorOpen: boolean;
  splitNodeIds: string[];
  splitTitle: string;
  splitObjective: string;
  splitReaderTakeaway: string;
  splitTimeScopeText: string;
  splitQuestionsText: string;
  mergeOptions: ChapterMergeOption[];
  mergeTargetChapterId: string;
  mergeTargetTitle: string;
  editingNodeId: string;
  editingNodeTitle: string;
  editingNodePurpose: string;
  editingNodeRequired: boolean;
  nodes: StructureNodeView[];
}

interface ChapterStructureContext {
  data: ChapterStructureState;
  setData(data: Partial<ChapterStructureState>): void;
  refreshWorkspaceOnly(): Promise<void>;
  openContractEditor(contract: MemoirChapterWorkspaceContract | null): void;
}

interface ChapterStructureDependencies {
  createContractRevision(
    projectId: string,
    chapterId: string,
    data: MemoirChapterContractRevisionRequest,
  ): Promise<MemoirChapterWorkspaceContract>;
  getBookPlan(projectId: string): Promise<MemoirBookPlan>;
  splitChapter(
    projectId: string,
    data: MemoirChapterStructureSplitRequest,
  ): Promise<MemoirChapterStructureMutationResponse>;
  mergeChapters(
    projectId: string,
    data: MemoirChapterStructureMergeRequest,
  ): Promise<MemoirChapterStructureMutationResponse>;
}

export function createChapterStructureActions(
  confirmDialog: ConfirmDialog,
  dependencies: ChapterStructureDependencies,
) {
  return {
    async loadChapterOptions(this: ChapterStructureContext) {
      if (!this.data.projectId || !this.data.chapterId) {
        return;
      }
      try {
        const plan = await dependencies.getBookPlan(this.data.projectId);
        const mergeOptions = chapterMergeOptions(plan, this.data.chapterId);
        const selected = mergeOptions.find(
          (option) => option.id === this.data.mergeTargetChapterId,
        );
        this.setData({
          mergeOptions,
          mergeTargetChapterId: selected?.id || "",
          mergeTargetTitle: selected?.title || "",
        });
      } catch (error) {
        if (routeAuthError(error, "me")) {
          return;
        }
        this.setData({
          mergeOptions: [],
          mergeTargetChapterId: "",
          mergeTargetTitle: "",
          topologyError: error instanceof Error ? error.message : "章节目录加载失败",
        });
      }
    },

    openContractEditor(
      this: ChapterStructureContext,
      contract: MemoirChapterWorkspaceContract | null,
    ) {
      const fields = contractEditorFields(contract);
      this.setData({
        contractEditorOpen: true,
        contractObjective: fields.objective,
        contractReaderTakeaway: fields.readerTakeaway,
        contractTimeScopeText: fields.timeScopeText,
        contractQuestionsText: fields.questionsText,
        contractActionStatus: "",
        error: "",
      });
    },

    async startContractEdit(this: ChapterStructureContext) {
      const contract = this.data.workspace?.contract || null;
      if (this.data.contractActionBusy || this.data.structureActionBusy) {
        return;
      }
      if (!contract || contract.status === "draft") {
        this.openContractEditor(contract);
        return;
      }
      const accepted = await confirmDialog(
        "调整章节结构",
        "保存修改时会创建新草稿；当前已确认版本和历史正文都会保留。",
        "开始调整",
      );
      if (!accepted) {
        return;
      }
      this.openContractEditor(contract);
      this.setData({
        contractActionStatus: "修改后保存时才会创建草稿版本",
      });
    },

    changeContractObjective(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ contractObjective: event.detail.value });
    },

    changeContractReaderTakeaway(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ contractReaderTakeaway: event.detail.value });
    },

    changeContractTimeScope(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ contractTimeScopeText: event.detail.value });
    },

    changeContractQuestions(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ contractQuestionsText: event.detail.value });
    },

    cancelContractEdit(this: ChapterStructureContext) {
      if (!this.data.contractActionBusy) {
        this.setData({ contractEditorOpen: false });
      }
    },

    async saveContractRevision(this: ChapterStructureContext) {
      if (this.data.contractActionBusy || this.data.structureActionBusy) {
        return;
      }
      const contract = this.data.workspace?.contract || null;
      const fields = {
        objective: this.data.contractObjective,
        readerTakeaway: this.data.contractReaderTakeaway,
        timeScopeText: this.data.contractTimeScopeText,
        questionsText: this.data.contractQuestionsText,
      };
      const idempotencyKey = chapterStructureIntentKey(
        "contract-revision",
        contract?.id || this.data.chapterId,
        fields,
      );
      let request;
      try {
        request = buildContractRevisionRequest(
          contract,
          fields,
          idempotencyKey,
        );
      } catch (error) {
        this.setData({ error: error instanceof Error ? error.message : "章节目标填写不完整" });
        return;
      }
      this.setData({
        contractActionBusy: true,
        contractActionStatus: "正在保存新的章节契约版本",
        error: "",
      });
      try {
        const revision = await dependencies.createContractRevision(
          this.data.projectId,
          this.data.chapterId,
          request,
        );
        await this.refreshWorkspaceOnly();
        this.setData({
          contractEditorOpen: false,
          contractActionStatus: `契约 v${revision.version} 已保存为草稿`,
        });
        wx.showToast({ title: "章节目标已保存", icon: "success" });
      } catch (error) {
        if (routeAuthError(error, "me")) {
          return;
        }
        this.setData({
          contractActionStatus: "",
          error: error instanceof Error ? error.message : "章节目标保存失败",
        });
      } finally {
        this.setData({ contractActionBusy: false });
      }
    },

    openSplitEditor(this: ChapterStructureContext) {
      const contract = this.data.workspace?.contract;
      if (
        !contract
        || contract.status !== "draft"
        || contract.nodes.length < 2
        || this.data.structureActionBusy
      ) {
        this.setData({
          topologyError: contract?.status === "confirmed"
            ? "请先创建可编辑的章节契约草稿"
            : "拆分章节前至少需要两个结构节点",
        });
        return;
      }
      const fields = splitEditorFields(contract);
      this.setData({
        splitEditorOpen: true,
        splitNodeIds: [],
        splitTitle: fields.title,
        splitObjective: fields.objective,
        splitReaderTakeaway: fields.readerTakeaway,
        splitTimeScopeText: fields.timeScopeText,
        splitQuestionsText: fields.questionsText,
        topologyActionStatus: "请选择要移到新章节的结构节点",
        topologyError: "",
        editingNodeId: "",
        editingNodeTitle: "",
        editingNodePurpose: "",
        editingNodeRequired: false,
        nodes: this.data.nodes.map((node) => ({ ...node, selectedForSplit: false })),
      });
    },

    cancelSplitEditor(this: ChapterStructureContext) {
      if (this.data.structureActionBusy) {
        return;
      }
      this.setData({
        splitEditorOpen: false,
        splitNodeIds: [],
        topologyActionStatus: "",
        nodes: this.data.nodes.map((node) => ({ ...node, selectedForSplit: false })),
      });
    },

    changeSplitNodeSelection(
      this: ChapterStructureContext,
      event: { detail: { value: string[] } },
    ) {
      const splitNodeIds = event.detail.value;
      const selected = new Set(splitNodeIds);
      this.setData({
        splitNodeIds,
        nodes: this.data.nodes.map((node) => ({
          ...node,
          selectedForSplit: selected.has(node.id),
        })),
        topologyActionStatus: splitNodeIds.length
          ? `已选择 ${splitNodeIds.length} 个节点`
          : "请选择要移到新章节的结构节点",
        topologyError: "",
      });
    },

    changeSplitTitle(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ splitTitle: event.detail.value });
    },

    changeSplitObjective(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ splitObjective: event.detail.value });
    },

    changeSplitReaderTakeaway(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ splitReaderTakeaway: event.detail.value });
    },

    changeSplitTimeScope(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ splitTimeScopeText: event.detail.value });
    },

    changeSplitQuestions(this: ChapterStructureContext, event: PageTextInputEvent) {
      this.setData({ splitQuestionsText: event.detail.value });
    },

    async submitChapterSplit(this: ChapterStructureContext) {
      const contract = this.data.workspace?.contract;
      if (!contract || contract.status !== "draft" || this.data.structureActionBusy) {
        return;
      }
      const fields = {
        title: this.data.splitTitle,
        objective: this.data.splitObjective,
        readerTakeaway: this.data.splitReaderTakeaway,
        timeScopeText: this.data.splitTimeScopeText,
        questionsText: this.data.splitQuestionsText,
      };
      const idempotencyKey = chapterStructureIntentKey(
        "chapter-split",
        contract.id,
        [...this.data.splitNodeIds].sort(),
        fields,
      );
      let request;
      try {
        request = buildSplitRequest(
          contract,
          this.data.splitNodeIds,
          fields,
          idempotencyKey,
        );
      } catch (error) {
        this.setData({
          topologyError: error instanceof Error ? error.message : "新章节信息填写不完整",
        });
        return;
      }
      this.setData({
        structureActionBusy: true,
        topologyActionStatus: "正在创建新章节并移动结构节点",
        topologyError: "",
      });
      try {
        const result = await dependencies.splitChapter(this.data.projectId, request);
        this.setData({ topologyActionStatus: "章节已拆分，正在打开新章节" });
        wx.redirectTo({
          url: pageUrl("/pages/chapter-workspace/index", {
            projectId: this.data.projectId,
            chapterId: result.target.chapter_id,
            tab: "structure",
          }),
        });
      } catch (error) {
        if (routeAuthError(error, "me")) {
          return;
        }
        this.setData({
          topologyActionStatus: "",
          topologyError: error instanceof Error ? error.message : "章节拆分失败",
        });
      } finally {
        this.setData({ structureActionBusy: false });
      }
    },

    changeMergeTarget(
      this: ChapterStructureContext,
      event: { detail: { value: string | number } },
    ) {
      const option = this.data.mergeOptions[Number(event.detail.value)];
      this.setData({
        mergeTargetChapterId: option?.id || "",
        mergeTargetTitle: option?.title || "",
        topologyError: "",
      });
    },

    async mergeCurrentChapter(this: ChapterStructureContext) {
      const contract = this.data.workspace?.contract;
      const target = this.data.mergeOptions.find(
        (option) => option.id === this.data.mergeTargetChapterId,
      );
      if (
        !contract
        || contract.status !== "draft"
        || !target
        || this.data.structureActionBusy
        || this.data.contractActionBusy
      ) {
        this.setData({
          topologyError: contract?.status === "confirmed"
            ? "请先创建可编辑的章节契约草稿"
            : "请先选择要合并到的章节",
        });
        return;
      }
      const accepted = await confirmDialog(
        "合并当前章节",
        `结构节点会移入“${target.title}”，当前章节将归档。历史版本会保留；合并后需要核对目标章节契约再确认。`,
        "确认合并",
      );
      if (!accepted) {
        return;
      }
      this.setData({
        structureActionBusy: true,
        topologyActionStatus: `正在合并到“${target.title}”`,
        topologyError: "",
      });
      try {
        const result = await dependencies.mergeChapters(this.data.projectId, {
          idempotency_key: chapterStructureIntentKey(
            "chapter-merge",
            contract.id,
            target.id,
          ),
          source_contract_id: contract.id,
          target_chapter_id: target.id,
        });
        this.setData({ topologyActionStatus: "章节已合并，正在打开保留章节" });
        wx.redirectTo({
          url: pageUrl("/pages/chapter-workspace/index", {
            projectId: this.data.projectId,
            chapterId: result.target.chapter_id,
            tab: "structure",
            editContract: "1",
          }),
        });
      } catch (error) {
        if (routeAuthError(error, "me")) {
          return;
        }
        this.setData({
          topologyActionStatus: "",
          topologyError: error instanceof Error ? error.message : "章节合并失败",
        });
      } finally {
        this.setData({ structureActionBusy: false });
      }
    },
  };
}
