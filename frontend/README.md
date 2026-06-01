# Pawtale Frontend

React + TypeScript 前端。当前主要承担写作工作台、项目类型分发、Binder、编辑器、资料编辑器和 AI 辅助面板。

## 技术栈

- React 19
- TypeScript
- Vite
- TailwindCSS
- Zustand
- TanStack Query
- Lucide React
- @dnd-kit

## 运行

```bash
cd frontend
npm install
npm run dev
```

开发服务默认运行在 http://localhost:5173，并通过 Vite proxy 调用后端 `/api`。

## 检查

```bash
npm run lint
npm run build
```

## 核心结构

```text
src/
├── api/index.ts
├── constants/aiModels.ts
├── domain/projectProfiles.ts
├── stores/appStore.ts
├── hooks/
│   ├── useProjectState.ts
│   ├── useReferenceEditor.ts
│   ├── useAIRevision.ts
│   └── project/
│       ├── useDocumentDraft.ts
│       ├── useProjectStructure.ts
│       ├── useProjectReferences.ts
│       └── useDraftRevision.ts
└── components/
    ├── Layout.tsx
    ├── WritingWorkspace.tsx
    ├── ProjectHome.tsx
    ├── Binder.tsx
    ├── binder/
    └── workspaces/
```

## 工作台边界

- `WritingWorkspace`：无项目时显示 `ProjectHome`，有项目时按 `project.type` 分发 workspace
- `WorkspaceShell`：共享布局骨架，负责 Binder、移动端目录、右侧面板和弹窗槽位
- `ProjectDraftWorkspace`：共享草稿工作台，装配编辑器、资料编辑器、AI 面板和 Inspector
- `NovelWorkspace`：小说工作台，开启 Inspector 和项目分析
- `ArticleWorkspace`：公众号工作台，复用共享草稿工作台
- `MemoirWorkspace`：回忆录工作台，复用共享草稿工作台并展示问题面板

## 状态和数据

- Zustand 保存当前项目、当前文档、当前资料、默认模型
- TanStack Query 管理项目列表和部分服务端数据刷新
- `useProjectState` 是项目工作台 facade
- 文档正文按需加载，切换文档/资料前保存当前脏草稿

## API

前端通过 `src/api/index.ts` 访问后端：

- `projectsApi`
- `authApi`
