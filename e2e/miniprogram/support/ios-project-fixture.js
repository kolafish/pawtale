function runIdentifier(now = Date.now, random = Math.random) {
  return `${now()}-${random().toString(16).slice(2, 8)}`;
}

class TestAccountAPI {
  constructor(config, options = {}) {
    this.account = config.account;
    this.baseUrl = config.httpBaseUrl;
    this.fetch = options.fetch || globalThis.fetch;
    this.cookie = "";
  }

  async login() {
    const response = await this.fetch(`${this.baseUrl}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(this.account),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      throw new Error(`真机项目夹具登录失败：HTTP ${response.status}`);
    }
    this.cookie = String(response.headers.get("set-cookie") || "").split(";")[0];
    if (!this.cookie) {
      throw new Error("真机项目夹具登录未返回会话 cookie");
    }
  }

  async request(path, options = {}) {
    if (!this.cookie) {
      await this.login();
    }
    const response = await this.fetch(`${this.baseUrl}${path}`, {
      method: options.method || "GET",
      headers: {
        Cookie: this.cookie,
        ...(options.body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: AbortSignal.timeout(options.timeoutMs || 15000),
    });
    if (!response.ok) {
      throw new Error(`真机项目夹具请求失败：${options.method || "GET"} ${path} HTTP ${response.status}`);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  listProjects() {
    return this.request("/api/mobile/memoir/projects");
  }

  activateProject(projectId) {
    return this.request(
      `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}/activate`,
      { method: "POST", body: {} },
    );
  }

  deleteProject(projectId) {
    return this.request(
      `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}`,
      { method: "DELETE" },
    );
  }
}

async function createIOSProjectFixture(config, options = {}) {
  const api = new TestAccountAPI(config, options);
  const baseline = await api.listProjects();
  if (!Array.isArray(baseline.projects) || baseline.projects.length === 0) {
    throw new Error(
      "测试账号需要预先保留一本基线回忆录；真机自动化不会创建后删除账号唯一的回忆录",
    );
  }
  const initialProject = baseline.projects.find(
    (project) => project.id === baseline.active_project_id,
  );
  if (!initialProject) {
    throw new Error("测试账号没有可恢复的活动回忆录，拒绝开始真机项目变更");
  }
  if (baseline.projects.filter((project) => project.title === initialProject.title).length !== 1) {
    throw new Error("活动回忆录名称在测试账号中不唯一，真机 UI 无法安全恢复基线");
  }
  const id = runIdentifier(options.now, options.random);
  const fixture = {
    api,
    baselineProjectIds: new Set(baseline.projects.map((project) => project.id)),
    createdProjectIds: new Set(),
    createdProjectTitles: new Set(),
    initialActiveProjectId: baseline.active_project_id,
    initialTitle: `E2E-iOS-${id}`,
    renamedTitle: `E2E-iOS-renamed-${id}`,
    cleaned: false,
    registerTitle(title) {
      this.createdProjectTitles.add(title);
    },
    async captureCreatedProject(title) {
      const current = await this.api.listProjects();
      const matches = current.projects.filter(
        (project) => !this.baselineProjectIds.has(project.id) && project.title === title,
      );
      if (matches.length !== 1) {
        throw new Error(`未能按本轮唯一标题确认新回忆录：匹配 ${matches.length} 本`);
      }
      this.createdProjectIds.add(matches[0].id);
      return matches[0];
    },
    async assertProject(projectId, title, active, expectedFields = {}) {
      const current = await this.api.listProjects();
      const project = current.projects.find((item) => item.id === projectId);
      if (!project || project.title !== title) {
        throw new Error("后端未保存真机 UI 中的回忆录变更");
      }
      if (active !== undefined && (current.active_project_id === projectId) !== active) {
        throw new Error(active ? "真机 UI 未激活目标回忆录" : "真机 UI 未恢复基线回忆录");
      }
      for (const [field, expected] of Object.entries(expectedFields)) {
        if (project[field] !== expected) {
          throw new Error(`后端未保存真机 UI 中的回忆录字段：${field}`);
        }
      }
      return project;
    },
    async assertDeleted(projectId) {
      const current = await this.api.listProjects();
      if (current.projects.some((project) => project.id === projectId)) {
        throw new Error("真机 UI 删除后，测试回忆录仍存在于后端");
      }
      this.createdProjectIds.delete(projectId);
    },
    async cleanup() {
      if (this.cleaned) {
        return;
      }
      const before = await this.api.listProjects();
      const removableIds = new Set(this.createdProjectIds);
      for (const project of before.projects) {
        if (
          !this.baselineProjectIds.has(project.id)
          && this.createdProjectTitles.has(project.title)
        ) {
          removableIds.add(project.id);
        }
      }
      if (
        this.initialActiveProjectId
        && before.active_project_id !== this.initialActiveProjectId
        && before.projects.some((project) => project.id === this.initialActiveProjectId)
      ) {
        await this.api.activateProject(this.initialActiveProjectId);
      }
      for (const projectId of removableIds) {
        if (before.projects.some((project) => project.id === projectId)) {
          await this.api.deleteProject(projectId);
        }
      }
      const after = await this.api.listProjects();
      const residue = after.projects.filter(
        (project) => removableIds.has(project.id)
          || (
            !this.baselineProjectIds.has(project.id)
            && this.createdProjectTitles.has(project.title)
          ),
      );
      if (residue.length > 0) {
        throw new Error(`真机项目夹具清理后仍残留 ${residue.length} 本测试回忆录`);
      }
      if (
        this.initialActiveProjectId
        && after.projects.some((project) => project.id === this.initialActiveProjectId)
        && after.active_project_id !== this.initialActiveProjectId
      ) {
        throw new Error("真机项目夹具清理后未恢复原活动回忆录");
      }
      this.cleaned = true;
    },
  };
  fixture.registerTitle(fixture.initialTitle);
  fixture.registerTitle(fixture.renamedTitle);
  return fixture;
}

module.exports = {
  TestAccountAPI,
  createIOSProjectFixture,
  runIdentifier,
};
