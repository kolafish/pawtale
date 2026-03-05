# Pawtale Frontend

## 技术栈

- React 19 + TypeScript + Vite
- TailwindCSS 样式
- Zustand 状态管理
- TanStack Query 数据获取
- Lucide React 图标

## 安装依赖

```bash
cd frontend
npm install
```

## 运行

```bash
npm run dev
```

访问 http://localhost:5173

## 构建

```bash
npm run build
```

## 核心功能

### 引导式创建向导
- 4步流程帮助用户创建故事大纲
- 支持选择 AI 模型（DeepSeek、Claude Opus 4.6、Claude Sonnet 4.6）
- 实时预览 AI 生成的大纲草稿

### 故事大纲管理
- 创建和编辑大纲
- 配置角色（类型、称呼、关系）
- 配置场景（可用场景、起始场景）
- 设置故事参数（情绪、风格、冲突、结局）

### 互动故事生成
- 基于大纲生成分支式故事
- 场景动态切换
- 角色进出管理
- 多结局支持

## 核心组件

- `OutlinePanel.tsx` - 故事大纲管理面板
- `OutlineGuidedWizard.tsx` - 引导式创建向导
- `StoryPreview.tsx` - 故事预览和生成
- `CharacterPanel.tsx` - 角色管理
- `ScenePanel.tsx` - 场景管理

## API 代理

开发环境下，Vite 自动将 `/api` 请求代理到 `http://localhost:8000`
