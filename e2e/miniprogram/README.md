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

语音测试启动仅监听 `127.0.0.1` 随机端口的内存 fixture，模拟后端 HTTP、Streaming
ASR、LLM 回应和 PCM TTS；开发者工具中的录音器输入也使用固定 PCM 帧。小程序仍走
真实页面绑定、授权同步、WebSocket 协议、语音状态机和 WebAudio 播放代码。该目标不
调用生产后端，也不需要真实 provider key 或麦克风输入。

## iOS 真机 smoke

这条链路使用 Appium + XCUITest + WebDriverAgent 控制 iPhone 上的真实微信界面，不依赖
开发者工具的 `miniprogram-automator` 真机桥。它覆盖微信“发现 → 小程序”、老余口述启动、
测试账号登录以及首页/记录/书稿/我的四个主 Tab；断言用户可见内容，不读取页面 `data`
或小程序存储。

首次准备：

1. 安装完整 Xcode，只需下载与 iPhone 对应的 iOS Platform；不需要 watchOS Platform。
2. iPhone 使用 USB 连接 Mac，保持解锁，并开启“开发者模式”和“UI 自动化”。
3. Xcode 中登录 Apple ID，使用个人 Team 签名 WebDriverAgent，并在 iPhone 上信任开发者。
4. iPhone 微信登录有老余口述开发版权限的账号，至少手动打开一次老余口述，使它出现在
   微信“小程序 → 最近使用”的第一位。若不是第一位，可设置
   `PAWTALE_E2E_IOS_RECENT_SLOT=1..4`。
5. 在 `.env.e2e.local` 填写 `PAWTALE_E2E_IOS_TEAM_ID`。只有连接多个 iPhone 时才需要
   填 `PAWTALE_E2E_IOS_UDID`；单台 USB 设备会从隧道注册表自动发现。

安装隔离的 XCUITest driver：

```bash
npm --prefix e2e/miniprogram run ios:setup
```

另开一个 Terminal 启动 RemoteXPC 隧道：

```bash
npm --prefix e2e/miniprogram run ios:tunnel
```

系统若询问密码，请在 Terminal 中输入 Mac 登录密码；输入时不会显示字符。隧道运行期间
保持该 Terminal 窗口开启。随后从仓库根目录执行：

```bash
./scripts/test-miniprogram-e2e.sh ios
```

测试会自动复用或启动本机 Appium server，并把 XCUITest driver 缓存在未跟踪的
`e2e/miniprogram/.appium/`。会话使用 10 次/秒的低速键入并按实时控件坐标输入，避免
微信小程序受控输入框在真机上丢字符。登录页截图会被跳过，失败 XML 会脱敏，账号和
密码不会写入产物或 Appium 普通命令日志。

真机 smoke 不创建、重命名或删除回忆录，也不会发送语音。首次系统权限弹窗、真实麦克风
采集、锁屏/后台切换、扬声器听感和弱网恢复仍需单独的人工体验测试；开发者工具目标继续
负责页面 `data`、存储恢复、WebSocket 协议和确定性语音 fixture 等更深层断言。

失败产物写入 `artifacts/`。测试只按本轮记录的 ID 或唯一运行标题精确清理数据，
不会模糊匹配名称，也不会修改测试账号中原有回忆录。
