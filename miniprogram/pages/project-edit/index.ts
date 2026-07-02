import type { MemoirGuidanceMode } from "../../domain/voice-chat";
import type { MemoirNarratorGender } from "../../services/memoir";
import { getMemoirIdentity } from "../../services/auth";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  activateMemoirProject,
  createMemoirProject,
  listMemoirProjects,
  updateMemoirProject,
} from "../../services/memoir";
import { memoirProjectProfile } from "../../services/memoir-project-profile";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      gender?: MemoirNarratorGender;
      mode?: MemoirGuidanceMode;
    };
  };
}

interface InputEvent {
  detail: {
    value: string;
  };
}

interface Option<T extends string> {
  value: T;
  label: string;
}

interface ProjectEditData {
  loading: boolean;
  saving: boolean;
  error: string;
  projectId: string;
  pageTitle: string;
  title: string;
  narratorGender: MemoirNarratorGender;
  narratorOrigin: string;
  narratorAgeText: string;
  defaultGuidanceMode: MemoirGuidanceMode;
  genderOptions: Array<Option<MemoirNarratorGender>>;
  guidanceOptions: Array<Option<MemoirGuidanceMode>>;
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

Page<ProjectEditData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  loadProject(projectId: string): Promise<void>;
  changeTitle(event: InputEvent): void;
  changeOrigin(event: InputEvent): void;
  changeAge(event: InputEvent): void;
  selectGender(event: DatasetEvent): void;
  selectGuidanceMode(event: DatasetEvent): void;
  saveProject(): Promise<void>;
  cancel(): void;
}>({
  data: {
    loading: false,
    saving: false,
    error: "",
    projectId: "",
    pageTitle: "新建回忆录",
    title: "",
    narratorGender: "unspecified",
    narratorOrigin: "",
    narratorAgeText: "",
    defaultGuidanceMode: "free_narration",
    genderOptions: [
      { value: "male", label: "男" },
      { value: "female", label: "女" },
      { value: "unspecified", label: "未填写" },
    ],
    guidanceOptions: [
      { value: "free_narration", label: "自己慢慢讲" },
      { value: "guided_interview", label: "小余陪着问" },
    ],
  },

  async onLoad(query) {
    const projectId = query?.projectId || "";
    if (projectId) {
      this.setData({ projectId, pageTitle: "编辑回忆录" });
      await this.loadProject(projectId);
      return;
    }
    this.setData({
      projectId: "",
      pageTitle: "新建回忆录",
      title: "",
      narratorGender: "unspecified",
      narratorOrigin: "",
      narratorAgeText: "",
      defaultGuidanceMode: "free_narration",
    });
  },

  async loadProject(projectId) {
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
        narratorGender: profile.narratorGender,
        narratorOrigin: profile.narratorOrigin,
        narratorAgeText: profile.narratorAge === null ? "" : String(profile.narratorAge),
        defaultGuidanceMode: profile.defaultGuidanceMode,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "回忆录加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  changeTitle(event) {
    this.setData({ title: event.detail.value, error: "" });
  },

  changeOrigin(event) {
    this.setData({ narratorOrigin: event.detail.value, error: "" });
  },

  changeAge(event) {
    this.setData({ narratorAgeText: event.detail.value.replace(/[^\d]/g, ""), error: "" });
  },

  selectGender(event) {
    const gender = event.currentTarget.dataset.gender;
    if (gender) {
      this.setData({ narratorGender: gender, error: "" });
    }
  },

  selectGuidanceMode(event) {
    const mode = event.currentTarget.dataset.mode;
    if (mode) {
      this.setData({ defaultGuidanceMode: mode, error: "" });
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
    this.setData({ saving: true, error: "" });
    const payload = {
      title,
      narrator_gender: this.data.narratorGender,
      narrator_origin: this.data.narratorOrigin.trim(),
      narrator_age: narratorAge,
      default_guidance_mode: this.data.defaultGuidanceMode,
    };
    try {
      if (this.data.projectId) {
        await updateMemoirProject(this.data.projectId, payload);
        trackAuditAction("project.update", {}, { projectId: this.data.projectId });
      } else {
        const project = await createMemoirProject(payload);
        await activateMemoirProject(project.id);
        trackAuditAction("project.create", {
          default_guidance_mode: this.data.defaultGuidanceMode,
        }, { projectId: project.id });
      }
      await getMemoirIdentity();
      wx.showToast({ title: "已保存", icon: "success" });
      wx.navigateBack({ delta: 1 });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
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
