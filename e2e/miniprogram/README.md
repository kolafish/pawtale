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

保持 iPhone 与电脑网络可达，并在 iPhone 微信中登录有该小程序调试权限的账号，然后执行：

```bash
./scripts/test-miniprogram-e2e.sh ios
```

脚本启动后，在开发者工具点击“真机调试 → 二维码真机调试”生成二维码并扫码；测试
会先等待真实的远程调试连接事件，再强制确认实际运行平台为 `ios`，然后用
测试账号验证登录、四个主 Tab、回忆录列表、网络和权限状态。该目标只读取已有基线
回忆录，不创建或删除业务数据，也不会把开发者工具模拟器结果当作真机通过。
真机目标默认以 `miniprogram/` 作为开发者工具项目根，使 RC 版真机编译器可以直接
找到 `app.json`；普通 `devtools` 目标仍使用仓库根入口。

连接等待时间可通过
`PAWTALE_E2E_IOS_CONNECT_TIMEOUT_MS` 调整，默认三分钟。首次运行仍可能需要在 iPhone
上确认调试、网络或隐私提示。麦克风采集、后台切换和听感属于需要真人操作/判断的
体验项，不会被这个只读 smoke 伪装成已自动验证。

旧版开发者工具若仍兼容 SDK 的 `Tool.enableRemoteDebug`，可设置
`PAWTALE_E2E_IOS_SDK_PAIRING=true`，并用 `PAWTALE_E2E_IOS_AUTO_CONNECT=true` 选择自动
真机调试。当前 RC 版默认使用更稳定的 GUI 编译 + 连接事件接管方式。

运行前请保存开发者工具中的编辑内容；脚本会先关闭同一路径的项目窗口，再以独立
自动化端口重新打开，避免连接到上一次异常退出遗留的 AppService。

失败产物写入 `artifacts/`。测试只按本轮记录的 ID 或唯一运行标题精确清理数据，
不会模糊匹配名称，也不会修改测试账号中原有回忆录。
