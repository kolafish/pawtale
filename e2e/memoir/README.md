# 长篇回忆录打印版端到端验收

使用 Pawtale 本机 Colima 测试环境，从仓库根目录手动运行。

```bash
./scripts/test-memoir-print-e2e.sh --colima
```

也可以使用已安装开发依赖的 `backend/venv` 和本机独立 ParadeDB。设置 `TEST_POSTGRES_DATABASE_URL` 后执行同一脚本，省略 `--colima`。后端测试 fixture 会迁移并清空专用测试库。PDF 检查使用 `uv` 准备独立的 Python 依赖，也可将 `PAWTALE_E2E_PDF_PYTHON` 指向已安装本目录依赖的解释器。默认后端 CI 不收集这个用例。

用例从空项目开始，在真实语音网关回放固定 PCM 与 ASR 结果，保存八场虚构真人访谈。后端使用固定模型回包，照常执行转写整理、章节归类、成文、组书和版本导出。第七场后通过现有编辑接口将一节设为不收录。第八场保存后、后台整理开始前，通过素材编辑接口更正第一场的事实。两次整理可以并行，验收同一批讲述只生成一个小节，并检查原始转写、旧书稿和新书稿各保留正确内容。运行还校验同一事件多次讲述的归属、误导性问题、记不清的年份，以及最终正文至少 6,000 个字母或数字字符（中文汉字计入）。

报告写到 `output/pdf/memoir-e2e/run-*/`，也可以通过 `PAWTALE_E2E_ARTIFACT_ROOT` 改变根目录。两份 PDF 及书稿 JSON 对应更正前后两次导出。`pdf-audit.json` 记录章、小节、自然段首行的物理页与位置，或记录第一个失败及所在页。页面预览以 PNG 保存。遇到后台失败时可查看 `backend.log`、`jobs.json` 和 `sources.json`。

入口覆盖应用的 HTTP 和 WebSocket 路由、数据库任务和 PDF 下载。小程序 UI、麦克风设备和真实模型的写作质量分别由现有设备测试和模型评测覆盖。
