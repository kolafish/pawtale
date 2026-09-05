# 故事树

展示现有书稿摘要、人生脉络、章节/小节、未归类讲述与素材入口。
`story` 使用 `StoryViewData`，由宿主调用纯函数 `storyViewData(snapshot)` 转换；
`largeText` 由宿主在显示页面时同步用户设置。

组件只渲染输入并发送事件，不读取账号、请求接口、轮询、导航或修改传入状态。
宿主负责维护章节展开状态、处理生成提醒、刷新数据和执行跳转。
`toggleStoryChapter` 返回新数据；首页的 `homeStorySnapshotData` 负责根据有类型的增长事件
标记新增与补充，并保留用户的展开选择。`showSummary` 控制书稿摘要，空树从人生总览开始。

| 事件 | detail | 宿主行为 |
| --- | --- | --- |
| `togglechapter` | `{ chapterId }` | 更新章节展开状态并回传 `story` |
| `readchapter` | `{ chapterId }` | 校验章节可读后打开阅读页 |
| `openchapter` | `{ chapterId, sectionId }` | 打开章节或指定小节的编辑入口 |
| `openmedia` | `{ chapterId }` | 打开章节素材；空 ID 表示未归类素材 |
| `openbook` | 无 | 打开书稿或整理进度 |
| `opentranscripts` | 无 | 打开全部讲述 |
| `managechapters` | 无 | 打开章节管理 |
| `focusgrowth` | `{ top }` | 仅响应用户「看看变化」，用视口坐标定位故事 |

组件的 `apply-shared` 样式隔离接收 `app.wxss` 的 UI 基础样式与主题变量，
自身样式不向宿主输出。根节点显式携带大字模式标记，使组件内部的字号和布局
不依赖宿主页的类名。未归类素材入口使用 `catchtap`，避免同时打开讲述列表。

宿主在展开目标章节并完成 `setData` 后调用组件 `focusGrowth(chapterId, sectionId?)`。
组件只测量现有节点并发出位置事件；不存在的目标不跳转。普通刷新不改变滚动位置。
`growthAnimating` 只用于本次可见期间新收到的事件，首屏、重复刷新和重新展开不重放。
界面出现提醒、播放动画和定位均不代表用户已确认；确认仍由宿主调用后端接口。

章节编号 `ordinal` 与小节存在标志 `hasSections` 在宿主转换快照时计算。
开发者工具组件 Object 属性中的嵌套数组会成为以索引为键的对象，
组件模板不依赖其 `length` 或循环索引的数值类型。
