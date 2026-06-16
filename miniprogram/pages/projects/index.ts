import type { MemoirProjectListResponse, Project } from "../../domain/memoir";
import { getMemoirIdentity } from "../../services/auth";
import {
  activateMemoirProject,
  createMemoirProject,
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
  editing: boolean;
  editTitle: string;
  updatedText: string;
}

interface ProjectsData {
  loading: boolean;
  busy: boolean;
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
  return response.projects.map((project) => ({
    ...project,
    active: project.id === response.active_project_id,
    editing: project.id === editingId,
    editTitle: project.id === editingId ? editTitle : project.title,
    updatedText: displayDate(project.last_opened_at || project.updated_at || project.created_at),
  }));
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
  openHome(): void;
}>({
  data: {
    loading: false,
    busy: false,
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

  async cancelRename() {
    await this.loadProjects();
  },

  openHome() {
    wx.switchTab({ url: "/pages/me/index" });
  },
});
