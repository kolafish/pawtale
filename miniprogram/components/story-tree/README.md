# 故事树

展示现有书稿摘要、人生脉络、章节/小节、未归类讲述与素材入口。
`story` 使用 `StoryViewData`，由宿主调用纯函数 `storyViewData(snapshot)` 转换；
`largeText` 由宿主在显示页面时同步用户设置。

组件只渲染输入并发送事件，不读取账号、请求接口、轮询、导航或修改传入状态。
宿主负责维护章节展开状态、处理生成提醒、刷新数据和执行跳转。
`toggleStoryChapter`、`highlightStoryGrowth` 返回新数据，可在不同宿主中复用。

| 事件 | detail | 宿主行为 |
| --- | --- | --- |
| `togglechapter` | `{ chapterId }` | 更新章节展开状态并回传 `story` |
| `readchapter` | `{ chapterId }` | 校验章节可读后打开阅读页 |
| `openchapter` | `{ chapterId, sectionId }` | 打开章节或指定小节的编辑入口 |
| `openmedia` | `{ chapterId }` | 打开章节素材；空 ID 表示未归类素材 |
| `openbook` | 无 | 打开书稿或整理进度 |
| `opentranscripts` | 无 | 打开全部讲述 |
| `managechapters` | 无 | 打开章节管理 |

组件的 `apply-shared` 样式隔离接收 `app.wxss` 的 UI 基础样式与主题变量，
自身样式不向宿主输出。根节点显式携带大字模式标记，使组件内部的字号和布局
不依赖宿主页的类名。未归类素材入口使用 `catchtap`，避免同时打开讲述列表。
