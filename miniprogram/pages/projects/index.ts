import type { MemoirProjectListResponse, Project } from "../../domain/memoir";
import { getMemoirIdentity } from "../../services/auth";
import {
  activateMemoirProject,
  createMemoirProject,
  deleteMemoirProject,
  listMemoirProjects,
  updateMemoirProject,
} from "../../services/memoir";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface InputEvent {
  detail: {
    value: string;
  };
}

interface ProjectItem extends Project {
  active: boolean;
  canDelete: boolean;
  editing: boolean;
  editTitle: string;
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

function projectItems(response: MemoirProjectListResponse, editingId = "", editTitle = ""): ProjectItem[] {
  const canDelete = response.projects.length > 1;
  return response.projects.map((project) => ({
    ...project,
    active: project.id === response.active_project_id,
    canDelete,
    editing: project.id === editingId,
    editTitle: project.id === editingId ? editTitle : project.title,
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
      confirmColor: "#b4234a",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

Page<ProjectsData, {
  onLoad(): Promise<void>;
  loadProjects(editingId?: string, editTitle?: string): Promise<void>;
  applyProjects(response: MemoirProjectListResponse, editingId?: string, editTitle?: string): void;
  createProject(): Promise<void>;
  activateProject(event: DatasetEvent): Promise<void>;
  startRename(event: DatasetEvent): void;
  changeRenameTitle(event: InputEvent): void;
  saveRename(event: DatasetEvent): Promise<void>;
  cancelRename(): Promise<void>;
  deleteProject(event: DatasetEvent): Promise<void>;
  openHome(): void;
}>({
  data: {
    loading: false,
    busy: false,
    deletingId: "",
    error: "",
    activeProjectId: "",
    projects: [],
  },

  async onLoad() {
    await this.loadProjects();
  },

  async loadProjects(editingId = "", editTitle = "") {
    this.setData({ loading: true, error: "" });
    try {
      await getMemoirIdentity();
      const response = await listMemoirProjects();
      this.applyProjects(response, editingId, editTitle);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "回忆录加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyProjects(response, editingId = "", editTitle = "") {
    this.setData({
      activeProjectId: response.active_project_id,
      projects: projectItems(response, editingId, editTitle),
    });
  },

  async createProject() {
    if (this.data.busy) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      const project = await createMemoirProject();
      await getMemoirIdentity();
      wx.showToast({ title: "已创建", icon: "success" });
      await this.loadProjects(project.id, project.title);
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "创建失败" });
    } finally {
      this.setData({ busy: false });
    }
  },

  async activateProject(event) {
    const id = event.currentTarget.dataset.id || "";
    if (!id || id === this.data.activeProjectId || this.data.busy) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      await activateMemoirProject(id);
      await getMemoirIdentity();
      wx.showToast({ title: "已切换", icon: "success" });
      await this.loadProjects();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "切换失败" });
    } finally {
      this.setData({ busy: false });
    }
  },

  startRename(event) {
    const id = event.currentTarget.dataset.id || "";
    const project = this.data.projects.find((item) => item.id === id);
    if (!project) {
      return;
    }
    this.setData({
      projects: this.data.projects.map((item) => ({
        ...item,
        editing: item.id === id,
        editTitle: item.id === id ? item.title : item.editTitle,
      })),
    });
  },

  changeRenameTitle(event) {
    this.setData({
      projects: this.data.projects.map((item) => item.editing
        ? { ...item, editTitle: event.detail.value }
        : item),
    });
  },

  async saveRename(event) {
    const id = event.currentTarget.dataset.id || "";
    const project = this.data.projects.find((item) => item.id === id);
    const title = project?.editTitle.trim() || "";
    if (!project || !title || this.data.busy) {
      this.setData({ error: "回忆录名称不能为空" });
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      await updateMemoirProject(id, { title });
      await getMemoirIdentity();
      wx.showToast({ title: "已保存", icon: "success" });
      await this.loadProjects();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
    } finally {
      this.setData({ busy: false });
    }
  },

  async deleteProject(event) {
    const id = event.currentTarget.dataset.id || "";
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

  async cancelRename() {
    await this.loadProjects();
  },

  openHome() {
    wx.switchTab({ url: "/pages/me/index" });
  },
});
