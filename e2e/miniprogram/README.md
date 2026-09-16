# 老余口述小程序 E2E

这里存放微信开发者工具和真机端到端测试。现有 `miniprogram/test/` 继续负责
page method、view model、service 和状态机测试；本目录只覆盖真实小程序运行时、
页面绑定、导航以及跨服务主链路。

## 本地准备

1. 安装并登录微信开发者工具。
2. 在顶部菜单“设置 → 安全设置”中开启“服务端口”。
3. 确保测试账号中至少保留一本基线回忆录，供切换和恢复当前项目使用。
4. 复制 `.env.e2e.local.example` 为 `.env.e2e.local`，填写专用测试账号。
5. 不要把账号、密码、微信票据或 provider key 提交到仓库。

服务端口由微信开发者工具管理，不写入测试配置。自动化 WebSocket 默认给每轮
运行分配一个空闲本地端口，也可通过 `PAWTALE_E2E_AUTOMATION_PORT` 固定。

## 运行

从仓库根目录执行：

```bash
./scripts/test-miniprogram-e2e.sh devtools
```

`devtools` 会顺序运行账号主链和确定性语音主链。只验证语音链路时可执行：

```bash
./scripts/test-miniprogram-e2e.sh voice
```

首页改版可单独运行不依赖后端账号的视觉状态矩阵：

```bash
npm --prefix e2e/miniprogram run test:home-visual
```

它仍要求微信开发者工具已经登录并开启服务端口，但不会读取 `.env.e2e.local` 中的测试账号，
页面数据使用本地注入，不依赖生产后端，也不会修改项目数据。测试在真实小程序渲染器中注入未登录、无项目、空项目、
已有故事、声纹授权、成长反馈、刷新/整理失败和大字长文案状态，检查横向溢出与 44px 主操作
点击区域，并把首屏和需要滚动的页面截图写入 `artifacts/foundation-<timestamp>/`。

语音测试启动仅监听 `127.0.0.1` 随机端口的内存 fixture，模拟后端 HTTP、Streaming
ASR、LLM 回应和 PCM TTS；开发者工具中的录音器输入也使用固定 PCM 帧。小程序仍走
真实页面绑定、授权同步、WebSocket 协议、语音状态机和 WebAudio 播放代码。该目标不
调用生产后端，也不需要真实 provider key 或麦克风输入。

## iOS 真机自动化

这条链路使用 Appium + XCUITest + WebDriverAgent 控制 iPhone 上的真实微信界面，不依赖
开发者工具的 `miniprogram-automator` 真机桥。它覆盖微信“发现 → 小程序”、老余口述启动、
测试账号退出与重新登录、首页/记录/书稿/我的四个主 Tab，以及回忆录取消新建、创建、
重命名、切换和删除；断言用户可见内容，不读取页面 `data` 或小程序存储。项目变更还会
通过测试账号的后端 API 二次核对，避免只根据最终 UI 状态判断成功。

首次准备：

1. 安装完整 Xcode，只需下载与 iPhone 对应的 iOS Platform；不需要 watchOS Platform。
2. iPhone 使用 USB 连接 Mac，保持解锁，并开启“开发者模式”和“UI 自动化”。
3. 无人值守运行时，在 iPhone“设置 → 通用 → 自动填充与密码”中关闭“自动填充密码和
   通行密钥”。也可以保持开启并使用有人值守模式：密码框弹出 Face ID 后在 Terminal
   提示的 45 秒内完成面容识别，脚本会等待系统界面退出后继续输入。
4. Xcode 中登录 Apple ID，使用个人 Team 签名 WebDriverAgent，并在 iPhone 上信任开发者。
5. iPhone 微信登录有老余口述开发版权限的账号，至少手动打开一次老余口述，使它出现在
   微信“小程序 → 最近使用”的第一位。若不是第一位，可设置
   `PAWTALE_E2E_IOS_RECENT_SLOT=1..4`。
6. 在 `.env.e2e.local` 填写 `PAWTALE_E2E_IOS_TEAM_ID`。只有连接多个 iPhone 时才需要
   填 `PAWTALE_E2E_IOS_UDID`；单台 USB 设备会从隧道注册表自动发现。
7. 测试账号至少保留一本已激活的基线回忆录，且它的名称在该账号中唯一；每轮结束会恢复
   这一本为活动回忆录。

从仓库根目录运行一条命令：

```bash
./scripts/test-miniprogram-e2e.sh ios
```

运行器会依次检查 Xcode、隔离的 XCUITest driver、后端健康状态和测试账号，随后按需启动
RemoteXPC 隧道与 Appium server。系统若显示 `Password:`，输入一次 Mac 登录密码并回车；
输入时不会显示字符。测试结束、失败或运行器异常退出时，本轮创建的隧道都会自动清理。
若已经有 USB 隧道，运行器只复用它，不会停止其他 Terminal 管理的进程。

需要连续调试并跨多次运行复用隧道时，仍可选用：

```bash
npm --prefix e2e/miniprogram run ios:tunnel
```

该命令同样使用受监控的 root supervisor；按 `Ctrl-C` 会安全停止，即使上层 Node 进程
意外退出也会自动回收。启动时只需输入一次 Mac 密码；保持这个 Terminal 和隧道运行，
后续多轮 `./scripts/test-miniprogram-e2e.sh ios` 会直接复用，不再逐轮请求 Mac 密码。
单独安装或核对 driver 可运行：

```bash
npm --prefix e2e/miniprogram run ios:setup
```

XCUITest driver 缓存在未跟踪的
`e2e/miniprogram/.appium/`。会话使用 10 次/秒的低速键入，并在写入前校验目标输入框的
Appium 元素身份，避免微信小程序受控输入框在真机上丢字符或串字段。登录页截图会被跳过，失败 XML 会脱敏，账号和
密码不会写入产物或 Appium 普通命令日志。

真机测试会临时创建一本带唯一运行标识的回忆录，完成重命名和切换后删除，并恢复运行前
的活动回忆录。运行前会记录基线项目 ID；测试失败时也只会清理不属于基线且标题或 ID
被本轮明确记录的项目，不会删除原有数据。它目前不会发送语音。首次系统权限弹窗、真实
麦克风采集、锁屏/后台切换、扬声器听感和弱网恢复仍需后续真机阶段覆盖；开发者工具目标
继续负责页面 `data`、存储恢复、WebSocket 协议和确定性语音 fixture 等更深层断言。

每轮产物写入 `artifacts/ios-ui-<timestamp>/`。`runner.json` 记录环境自检、隧道、Appium、
账号/项目 UI 主链和资源清理各阶段的耗时及结果；`run.json` 记录真机 UI 断言。产物目录不跟踪，
账号密码会从错误与报告中脱敏，无法确认为老余口述的微信页面不会保存 XML 或截图。
测试只按本轮记录的 ID 或唯一运行标题精确清理数据，
不会模糊匹配名称，也不会修改测试账号中原有回忆录。
