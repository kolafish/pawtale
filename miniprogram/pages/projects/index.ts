import type { MemoirProjectListResponse, Project } from "../../domain/memoir";
import { getMemoirIdentity } from "../../services/auth";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  activateMemoirProject,
  deleteMemoirProject,
  listMemoirProjects,
} from "../../services/memoir";
import { projectProfileSummary } from "../../services/memoir-project-profile";
import {
  datasetString,
  pageUrl,
  type PageDatasetEvent,
} from "../../services/page-input";
import { definePage } from "../../services/page-definition";

interface ProjectDataset {
  id?: string;
}

type DatasetEvent = PageDatasetEvent<ProjectDataset>;

interface ProjectItem extends Project {
  active: boolean;
  canDelete: boolean;
  profileSummary: string;
  updatedText: string;
  wordCountText: string;
}

interface ProjectsData {
  loading: boolean;
  busy: boolean;
  deletingId: string;
  error: string;
  activeProjectId: string;
  projects: ProjectItem[];
}

function displayDate(value?: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function projectItems(response: MemoirProjectListResponse): ProjectItem[] {
  const canDelete = response.projects.length > 1;
  return response.projects.map((project) => ({
    ...project,
    active: project.id === response.active_project_id,
    canDelete,
    profileSummary: projectProfileSummary(project),
    updatedText: displayDate(project.last_opened_at || project.updated_at || project.created_at),
    wordCountText: `${project.total_word_count || 0} 字`,
  }));
}

function confirmDeleteProject(project: ProjectItem): Promise<boolean> {
  const activeHint = project.active ? "这是当前使用中的回忆录，删除后会自动切换到最近一本。" : "";
  return new Promise((resolve) => {
    wx.showModal({
      title: "删除回忆录",
      content: `确定删除「${project.title}」吗？${activeHint}删除后不可恢复。`,
      confirmText: "删除",
      confirmColor: "#b84a5a",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

function chooseProjectMoreAction(): Promise<number | null> {
  return new Promise((resolve) => {
    wx.showActionSheet({
      itemList: ["删除回忆录"],
      success: (result) => resolve(result.tapIndex),
      fail: () => resolve(null),
    });
  });
}

definePage<ProjectsData>()({
  data: {
    loading: false,
    busy: false,
    deletingId: "",
    error: "",
    activeProjectId: "",
    projects: [] as ProjectItem[],
  },

  async onLoad() {
    await this.loadProjects();
  },

  async onShow() {
    if (!this.data.loading && !this.data.busy) {
      await this.loadProjects();
    }
  },

  async loadProjects() {
    this.setData({ loading: true, error: "" });
    try {
      await getMemoirIdentity();
      const response = await listMemoirProjects();
      this.applyProjects(response);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "回忆录加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyProjects(response: MemoirProjectListResponse) {
    this.setData({
      activeProjectId: response.active_project_id,
      projects: projectItems(response),
    });
  },

  createProject() {
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  async activateProject(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id || id === this.data.activeProjectId || this.data.busy) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      await activateMemoirProject(id);
      await getMemoirIdentity();
      trackAuditAction("project.activate", {}, { projectId: id });
      wx.showToast({ title: "已切换", icon: "success" });
      await this.loadProjects();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "切换失败" });
    } finally {
      this.setData({ busy: false });
    }
  },

  openProjectEdit(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id) {
      return;
    }
    wx.navigateTo({ url: pageUrl("/pages/project-edit/index", { projectId: id }) });
  },

  async openProjectMore(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const project = this.data.projects.find((item) => item.id === id);
    if (!project || this.data.busy) {
      return;
    }
    if (!project.canDelete) {
      this.setData({ error: "至少保留一本回忆录" });
      return;
    }
    const actionIndex = await chooseProjectMoreAction();
    if (actionIndex !== 0) {
      return;
    }
    await this.deleteProjectById(id);
  },

  async deleteProjectById(id: string) {
    const project = this.data.projects.find((item) => item.id === id);
    if (!project || this.data.busy) {
      return;
    }
    if (!project.canDelete) {
      this.setData({ error: "至少保留一本回忆录" });
      return;
    }
    const confirmed = await confirmDeleteProject(project);
    if (!confirmed) {
      return;
    }
    this.setData({ busy: true, deletingId: id, error: "" });
    try {
      await deleteMemoirProject(id);
      await getMemoirIdentity();
      wx.showToast({ title: "已删除", icon: "success" });
      await this.loadProjects();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      this.setData({ busy: false, deletingId: "" });
    }
  },
});
