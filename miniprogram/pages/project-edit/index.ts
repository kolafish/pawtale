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
  defaultNarrationSessionFields,
  memoirProjectProfile,
} from "../../services/memoir-project-profile";
import {
  datasetChoice,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
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
const PAGE_CLASS = "project-edit-page ui-page-shell ui-accessible-page";

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
  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    saving: false,
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
    this.setData(accessibilityPagePatch(PAGE_CLASS));
  },

  async onLoad(query?: PageQuery) {
    this.pendingCreatedProject = null;
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
  },

  async loadProject(projectId: string) {
    this.setData({ loading: true, error: "" });
    try {
      const response = await listMemoirProjects();
      const project = response.projects.find((item) => item.id === projectId);
      if (!project) {
        this.setData({ error: "回忆录不存在" });
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
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "回忆录加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  changeTitle(event: InputEvent) {
    this.setData({ title: event.detail.value, error: "" });
  },

  changeNarratorName(event: InputEvent) {
    this.setData({ narratorName: event.detail.value, error: "" });
  },

  changeOrigin(event: InputEvent) {
    this.setData({ narratorOrigin: event.detail.value, error: "" });
  },

  changeAge(event: InputEvent) {
    this.setData({ narratorAgeText: event.detail.value.replace(/[^\d]/g, ""), error: "" });
  },

  selectGender(event: DatasetEvent) {
    const gender = datasetChoice(event, "gender", GENDER_VALUES, this.data.narratorGender);
    if (gender) {
      this.setData({ narratorGender: gender, error: "" });
    }
  },

  selectNarrationMode(event: DatasetEvent) {
    const mode = datasetChoice(event, "mode", NARRATION_VALUES, this.data.defaultNarrationMode);
    if (mode) {
      this.setData({ defaultNarrationMode: mode, error: "" });
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
      this.setData({ transcriptPolishLevel: level, error: "" });
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
      this.setData({ shareVideoStyle: style, error: "" });
    }
  },

  async saveProject() {
    if (this.data.saving || this.data.loading) {
      return;
    }
    const title = this.data.title.trim();
    if (!title) {
      this.setData({ error: "回忆录名称不能为空" });
      return;
    }
    let narratorAge: number | null;
    try {
      narratorAge = ageFromText(this.data.narratorAgeText);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "年龄格式不正确" });
      return;
    }
    const narrationFields = defaultNarrationSessionFields(this.data.defaultNarrationMode);
    this.setData({ saving: true, error: "" });
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
      wx.showToast({ title: "已保存", icon: "success" });
      if (createdProject) returnToHome();
      else wx.navigateBack({ delta: 1 });
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存失败";
      this.setData({ error: this.pendingCreatedProject && !this.data.projectId
        ? `回忆录已创建，${message}；请重试完成设置`
        : message });
    } finally {
      this.setData({ saving: false });
    }
  },

  cancel() {
    wx.navigateBack({ delta: 1 });
  },
});

export {
  ageFromText,
};
