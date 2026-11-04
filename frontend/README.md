# Pawtale Web Frontend

React + TypeScript Web 工作台，提供登录、项目管理、小说/公众号写作、回忆录精细编辑和管理员审计。

## 运行与检查

```bash
cd frontend
npm install
npm run dev

npm run lint
npm test
npm run build
```

开发服务器默认运行在 <http://localhost:5173>，通过 Vite proxy 访问后端 `/api`。

## 当前功能

### 共享外壳

- 生产 Web 仅允许已启用密码登录的管理员使用邮箱密码，开发和测试环境支持邮箱注册与登录，登录后可恢复 session；
- 项目列表、打开、重命名和删除；
- 管理员可选择默认 AI 模型；
- 管理员审计视图；
- Error Boundary 和 QueryClient。

Web 的“新建项目”当前直接创建小说项目；既有公众号和回忆录项目仍会按类型打开对应工作区。回忆录的主要创建入口在微信小程序。

### 小说与公众号

- Binder 文档树和资料区；
- 文档/分组新增、重命名、删除、排序和跨组移动；
- 正文按需加载、3 秒自动保存和 Cmd+S；
- 人物、地点、笔记和大纲资料；
- AI 续写、修改、diff review、多模型对比和项目分析；
- 项目导出和 GitHub 单向备份。

### 回忆录

`MemoirWorkspace` 使用独立界面，不复用小说草稿工作台：

- 素材校对：搜索、排序、状态筛选、音频回放、转写编辑；
- 高级工具：批量校正、问答关系、方言词表；
- 章节目录：浏览已有目录、章节顺序和来源素材；
- 章节稿：查看来源和版本。有编辑权限且目录已确认时，可按小节修改当前正文并保存。历史稿及缺少小节结构的旧稿只读；
- 全书：编辑书名、副标题、口述者、整理者、前言、后记、封底语和封面风格，预览和导出 PDF/TXT；
- URL 保留当前回忆录视图和选中素材/章节；
- 视图切换、浏览器返回和关闭页面前保护未保存内容。

## 代码结构

```text
src/
├── App.tsx
├── api/index.ts
├── constants/aiModels.ts
├── domain/
├── hooks/
├── stores/appStore.ts
├── types/index.ts
├── utils/
└── components/
    ├── AuthView.tsx
    ├── Layout.tsx
    ├── ProjectHome.tsx
    ├── WritingWorkspace.tsx
    ├── workspaces/
    ├── memoir/
    ├── binder/
    └── audit/
```

## 组件边界

- `WritingWorkspace`：根据 `project.type` 分发工作区；
- `ProjectDraftWorkspace`：小说/公众号共享的草稿装配层；
- `MemoirWorkspace`：回忆录四个一级编辑视图；
- `useProjectState`：共享写作工作区 facade；
- `useDocumentDraft`：正文加载、脏状态和保存；
- `useProjectStructure`：分组/文档 CRUD 与排序；
- `useProjectReferences`：资料 CRUD；
- `useAIRevision`：AI 修改与 diff review；
- `src/api/index.ts`：显式的服务端 transport。

产品差异进入 workspace、profile、domain 和后端 service，回忆录使用独立工作区。

## 状态和数据

- Zustand 保存客户端选中状态与默认模型；
- TanStack Query 管理服务端缓存和 mutation；
- 文档正文和回忆录编辑数据按需加载；
- mutation 成功后更新或失效对应 query；
- 可编辑项目和只读项目使用后端返回的 permission 字段控制操作；
- API 错误通过统一工具转成用户可理解的提示。
