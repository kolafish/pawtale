import {
  DEFAULT_NARRATION_MODE_OPTIONS,
  type DefaultNarrationMode,
} from "../../domain/memoir-narration-mode";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import type { MemoirTranscriptPolishLevel } from "../../domain/memoir";
import {
  MEMOIR_SHARE_VIDEO_STYLES,
  normalizeMemoirShareVideoStyle,
  type MemoirShareVideoStyle,
  type MemoirShareVideoStyleOption,
} from "../../domain/memoir-transcript";
import type { MemoirNarratorGender, MemoirProjectInput } from "../../services/memoir";
import { getMemoirIdentity } from "../../services/auth";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  activateMemoirProject,
  createMemoirProject,
  listMemoirProjects,
  updateMemoirProject,
} from "../../services/memoir";
import { markHomeLaunchProjectCreated } from "../../services/home-launch-state";
import {
  getMemoirBookShareStatus,
  revokeMemoirBookShare,
  updateMemoirBookShare,
} from "../../services/memoir-book-api";
import { ApiError, optionalApiResource } from "../../services/request";
import {
  defaultNarrationSessionFields,
  memoirProjectProfile,
} from "../../services/memoir-project-profile";
import {
  datasetChoice,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
  type PageSwitchEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { returnToHome } from "../../services/navigation";
import {
  transcriptPolishLevel,
  TRANSCRIPT_POLISH_LEVELS,
  TRANSCRIPT_POLISH_OPTIONS,
  type TranscriptPolishOption,
} from "../../services/transcript-polish-settings";

interface ProjectEditDataset {
  gender?: string;
  mode?: string;
  polish?: string;
  videoStyle?: string;
}

type DatasetEvent = PageDatasetEvent<ProjectEditDataset>;
type InputEvent = PageTextInputEvent;

interface Option<T extends string> {
  value: T;
  label: string;
}

interface ProjectEditData {
  pageClass: string;
  loading: boolean;
  saving: boolean;
  hasUnsavedChanges: boolean;
  actionDockHeight: number;
  error: string;
  projectId: string;
  pageTitle: string;
  title: string;
  narratorName: string;
  narratorGender: MemoirNarratorGender;
  narratorOrigin: string;
  narratorAgeText: string;
  defaultNarrationMode: DefaultNarrationMode;
  transcriptPolishLevel: MemoirTranscriptPolishLevel;
  shareVideoStyle: MemoirShareVideoStyle;
  bookShareEnabled: boolean;
  bookShareState: "loading" | "ready" | "unavailable" | "error";
  bookShareBusy: boolean;
  bookShareError: string;
  bookSharePendingTarget: boolean | null;
  genderOptions: Array<Option<MemoirNarratorGender>>;
  narrationOptions: Array<Option<DefaultNarrationMode> & { description: string }>;
  transcriptPolishOptions: TranscriptPolishOption[];
  shareVideoStyleOptions: MemoirShareVideoStyleOption[];
}

const GENDER_VALUES: readonly MemoirNarratorGender[] = ["male", "female", "unspecified"];
const NARRATION_VALUES: readonly DefaultNarrationMode[] = [
  "guided_interview",
  "book_completion",
  "human_interview",
];
type ProjectEditForm = Pick<ProjectEditData,
  "title" | "narratorName" | "narratorGender" | "narratorOrigin" | "narratorAgeText"
  | "defaultNarrationMode" | "transcriptPolishLevel" | "shareVideoStyle"
>;

const PAGE_CLASS = "project-edit-page ui-page-shell ui-accessible-page ui-fixed-action-page";

function formSnapshot(form: ProjectEditForm): string {
  return JSON.stringify([
    form.title, form.narratorName, form.narratorGender, form.narratorOrigin,
    form.narratorAgeText, form.defaultNarrationMode, form.transcriptPolishLevel, form.shareVideoStyle,
  ]);
}

function ageFromText(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > 130) {
    throw new Error("年龄需要填写 0 到 130 之间的整数");
  }
  return parsed;
}

definePage<ProjectEditData>()({
  pendingCreatedProject: null as { id: string; title: string; input: MemoirProjectInput } | null,
  savedFormSnapshot: "",
  unloadAlertEnabled: false,
  unloaded: false,
  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    saving: false,
    hasUnsavedChanges: false,
    actionDockHeight: 0,
    error: "",
    projectId: "",
    pageTitle: "新建回忆录",
    title: "",
    narratorName: "",
    narratorGender: "unspecified",
    narratorOrigin: "",
    narratorAgeText: "",
    defaultNarrationMode: "guided_interview",
    transcriptPolishLevel: transcriptPolishLevel(),
    shareVideoStyle: "illustrated",
    bookShareEnabled: false,
    bookShareState: "unavailable",
    bookShareBusy: false,
    bookShareError: "",
    bookSharePendingTarget: null,
    genderOptions: [
      { value: "male", label: "男" },
      { value: "female", label: "女" },
      { value: "unspecified", label: "未填写" },
    ],
    narrationOptions: DEFAULT_NARRATION_MODE_OPTIONS,
    transcriptPolishOptions: TRANSCRIPT_POLISH_OPTIONS,
    shareVideoStyleOptions: MEMOIR_SHARE_VIDEO_STYLES,
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS), () => this.measureActionDock());
    if (this.data.projectId && !this.data.loading && !this.data.bookShareBusy) return this.loadBookShareSetting();
  },

  async onLoad(query?: PageQuery) {
    this.unloaded = false;
    this.pendingCreatedProject = null;
    this.markFormSaved();
    const projectId = queryParam(query, "projectId");
    if (projectId) {
      this.setData({ projectId, pageTitle: "编辑回忆录" });
      setPageNavigationTitle("编辑回忆录");
      await this.loadProject(projectId);
      return;
    }
    this.setData({
      projectId: "",
      pageTitle: "新建回忆录",
      title: "",
      narratorName: "",
      narratorGender: "unspecified",
      narratorOrigin: "",
      narratorAgeText: "",
      defaultNarrationMode: "guided_interview",
      transcriptPolishLevel: transcriptPolishLevel(),
      shareVideoStyle: "illustrated",
    });
    setPageNavigationTitle("新建回忆录");
    this.markFormSaved();
  },

  onUnload() {
    this.unloaded = true;
    this.disableUnloadAlert();
  },

  onReady() {
    this.measureActionDock();
  },

  onResize() {
    this.measureActionDock();
  },

  measureActionDock() {
    if (this.unloaded) return;
    wx.createSelectorQuery?.().select("#project-edit-actions").boundingClientRect((rect) => {
      if (rect && !this.unloaded) this.setData({ actionDockHeight: Math.ceil(rect.height) + 16 });
    }).exec();
  },

  setFormError(error: string) {
    this.setData({ error }, () => this.measureActionDock());
  },

  markFormSaved() {
    this.savedFormSnapshot = formSnapshot(this.data);
    this.setData({ hasUnsavedChanges: false });
    this.disableUnloadAlert();
  },

  disableUnloadAlert() {
    if (!this.unloadAlertEnabled) return;
    wx.disableAlertBeforeUnload?.();
    this.unloadAlertEnabled = false;
  },

  syncUnloadAlert() {
    if (this.data.hasUnsavedChanges === this.unloadAlertEnabled) return;
    if (this.data.hasUnsavedChanges) {
      if (!wx.enableAlertBeforeUnload) return;
      wx.enableAlertBeforeUnload({ message: "当前修改尚未完成保存，确定离开吗？" });
      this.unloadAlertEnabled = true;
    } else {
      this.disableUnloadAlert();
    }
  },

  updateForm(patch: Partial<ProjectEditForm>) {
    if (this.data.loading || this.data.saving) return;
    const hadError = Boolean(this.data.error);
    const hasUnsavedChanges = formSnapshot({ ...this.data, ...patch }) !== this.savedFormSnapshot;
    this.setData({ ...patch, hasUnsavedChanges, error: "" }, () => {
      if (hadError) this.measureActionDock();
    });
    this.syncUnloadAlert();
  },

  async loadProject(projectId: string) {
    this.setData({ loading: true, error: "" });
    try {
      const response = await listMemoirProjects();
      const project = response.projects.find((item) => item.id === projectId);
      if (!project) {
        this.setFormError("回忆录不存在");
        return;
      }
      const profile = memoirProjectProfile(project);
      this.setData({
        title: project.title,
        narratorName: profile.narratorName,
        narratorGender: profile.narratorGender,
        narratorOrigin: profile.narratorOrigin,
        narratorAgeText: profile.narratorAge === null ? "" : String(profile.narratorAge),
        defaultNarrationMode: profile.defaultNarrationMode,
        transcriptPolishLevel: transcriptPolishLevel(project),
        shareVideoStyle: normalizeMemoirShareVideoStyle(project.metadata_json?.share_video_style),
      });
      this.markFormSaved();
      await this.loadBookShareSetting();
    } catch (error) {
      this.setFormError(error instanceof Error ? error.message : "回忆录加载失败");
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadBookShareSetting(): Promise<boolean> {
    if (!this.data.projectId || this.data.bookShareState === "loading") return false;
    this.setData({ bookShareState: "loading", bookShareError: "" });
    try {
      const status = await optionalApiResource(getMemoirBookShareStatus(this.data.projectId));
      this.setData({ bookShareEnabled: status?.active ?? false, bookShareState: status ? "ready" : "unavailable",
        bookSharePendingTarget: !status || status.active === this.data.bookSharePendingTarget ? null : this.data.bookSharePendingTarget });
      return true;
    } catch (error) {
      this.setData({ bookShareState: "error", bookShareError: error instanceof Error ? error.message : "分享状态加载失败" });
      return false;
    }
  },

  async toggleBookShare(event: PageSwitchEvent) {
    const enabled = event.detail.value;
    const previous = this.data.bookShareEnabled;
    if (!this.data.projectId || this.data.bookShareState !== "ready" || this.data.bookShareBusy || this.data.saving || enabled === previous) return;
    this.setData({ bookShareEnabled: enabled, bookShareBusy: true, bookShareError: "" });
    try {
      if (enabled) await updateMemoirBookShare(this.data.projectId);
      else await revokeMemoirBookShare(this.data.projectId);
      this.setData({ bookSharePendingTarget: null });
    } catch (error) {
      const message = error instanceof Error ? error.message : "分享设置失败";
      const rejected = error instanceof ApiError && error.statusCode >= 400 && error.statusCode < 500 && error.statusCode !== 408;
      if (rejected) {
        this.setData({ bookShareEnabled: previous, bookShareError: message });
      } else {
        // A GET is a snapshot; the timed-out write may still be pending.
        this.setData({ bookSharePendingTarget: enabled });
        const loaded = await this.loadBookShareSetting();
        this.setData({ bookShareError: loaded
          ? ""
          : `操作结果尚未确认，请重新读取分享状态。${this.data.bookShareError}` });
      }
    } finally {
      this.setData({ bookShareBusy: false });
    }
  },

  changeTitle(event: InputEvent) {
    this.updateForm({ title: event.detail.value });
  },

  changeNarratorName(event: InputEvent) {
    this.updateForm({ narratorName: event.detail.value });
  },

  changeOrigin(event: InputEvent) {
    this.updateForm({ narratorOrigin: event.detail.value });
  },

  changeAge(event: InputEvent) {
    this.updateForm({ narratorAgeText: event.detail.value.replace(/[^\d]/g, "") });
  },

  selectGender(event: DatasetEvent) {
    const gender = datasetChoice(event, "gender", GENDER_VALUES, this.data.narratorGender);
    if (gender) {
      this.updateForm({ narratorGender: gender });
    }
  },

  selectNarrationMode(event: DatasetEvent) {
    const mode = datasetChoice(event, "mode", NARRATION_VALUES, this.data.defaultNarrationMode);
    if (mode) {
      this.updateForm({ defaultNarrationMode: mode });
    }
  },

  selectTranscriptPolishLevel(event: DatasetEvent) {
    const level = datasetChoice(
      event,
      "polish",
      TRANSCRIPT_POLISH_LEVELS,
      this.data.transcriptPolishLevel,
    );
    if (level) {
      this.updateForm({ transcriptPolishLevel: level });
    }
  },

  selectShareVideoStyle(event: DatasetEvent) {
    const style = datasetChoice(
      event,
      "videoStyle",
      MEMOIR_SHARE_VIDEO_STYLES.map((option) => option.value),
      this.data.shareVideoStyle,
    );
    if (style) {
      this.updateForm({ shareVideoStyle: style });
    }
  },

  async saveProject() {
    if (this.data.saving || this.data.loading || this.data.bookShareBusy) {
      return;
    }
    const title = this.data.title.trim();
    if (!title) {
      this.setFormError("回忆录名称不能为空");
      return;
    }
    let narratorAge: number | null;
    try {
      narratorAge = ageFromText(this.data.narratorAgeText);
    } catch (error) {
      this.setFormError(error instanceof Error ? error.message : "年龄格式不正确");
      return;
    }
    const narrationFields = defaultNarrationSessionFields(this.data.defaultNarrationMode);
    this.setData({ saving: true, error: "" }, () => this.measureActionDock());
    const payload: MemoirProjectInput = {
      title,
      elder_name: this.data.narratorName.trim(),
      narrator_gender: this.data.narratorGender,
      narrator_origin: this.data.narratorOrigin.trim(),
      narrator_age: narratorAge,
      default_guidance_mode: narrationFields.guidanceMode,
      default_interview_type: narrationFields.interviewType,
      default_session_goal: narrationFields.sessionGoal,
      transcript_polish_level: this.data.transcriptPolishLevel,
      share_video_style: this.data.shareVideoStyle,
    };
    try {
      let createdProject = this.pendingCreatedProject;
      if (this.data.projectId) {
        await updateMemoirProject(this.data.projectId, payload);
        trackAuditAction("project.update", {}, { projectId: this.data.projectId });
      } else {
        if (createdProject) {
          if (JSON.stringify(createdProject.input) !== JSON.stringify(payload)) {
            const updated = await updateMemoirProject(createdProject.id, payload);
            createdProject = { id: updated.id, title: updated.title, input: payload };
            this.pendingCreatedProject = createdProject;
          }
        } else {
          const project = await createMemoirProject(payload);
          createdProject = { id: project.id, title: project.title, input: payload };
          this.pendingCreatedProject = createdProject;
          trackAuditAction("project.create", {
            default_narration_mode: this.data.defaultNarrationMode,
          }, { projectId: project.id });
        }
        await activateMemoirProject(createdProject.id);
      }
      const identity = await getMemoirIdentity();
      if (createdProject) {
        markHomeLaunchProjectCreated({
          userId: identity.id,
          projectId: createdProject.id,
          projectTitle: createdProject.title,
        });
      }
      this.pendingCreatedProject = null;
      if (this.unloaded) return;
      this.markFormSaved();
      wx.showToast({ title: "已保存", icon: "success" });
      if (createdProject) returnToHome();
      else wx.navigateBack({ delta: 1 });
    } catch (error) {
      if (this.unloaded) return;
      const message = error instanceof Error ? error.message : "保存失败";
      this.setFormError(this.pendingCreatedProject && !this.data.projectId
        ? `回忆录已创建，${message}；请重试完成设置`
        : message);
    } finally {
      if (!this.unloaded) this.setData({ saving: false });
    }
  },

  cancel() {
    if (this.data.saving || this.data.loading || this.data.bookShareBusy) return;
    const leave = () => {
      this.disableUnloadAlert();
      wx.navigateBack({ delta: 1, fail: () => this.syncUnloadAlert() });
    };
    if (!this.data.hasUnsavedChanges) {
      leave();
      return;
    }
    wx.showModal({
      title: "放弃当前修改？",
      content: "回忆录资料的修改还没有保存，离开后这些修改会丢失。",
      confirmText: "放弃修改",
      cancelText: "继续编辑",
      success: (result) => { if (result.confirm) leave(); },
    });
  },
});

export {
  ageFromText,
};
