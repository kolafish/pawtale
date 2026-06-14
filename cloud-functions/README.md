# Pawtale WeChat Cloud Functions

微信小程序上线链路使用云函数、云数据库和云存储。当前云开发环境：

```text
cloud1-d6gmxffp0ea48aa1a
```

## Functions

```text
memoir-api
```

`memoir-api` 是回忆录小程序的唯一业务入口。小程序通过 `wx.cloud.callFunction` 发送统一请求：

```js
{
  path: "/api/mobile/memoir/project",
  method: "GET",
  data: {}
}
```

云函数返回统一响应：

```js
{
  statusCode: 200,
  data: {}
}
```

collection schema 和建议索引见 [memoir-api/docs/collections.md](memoir-api/docs/collections.md)。

## Local Checks

```bash
node --test cloud-functions/memoir-api/test/*.test.js
```

## Deploy

用微信开发者工具打开仓库根目录 `Pawtale/` 后，在云开发面板中选择环境 `cloud1`，上传并部署 `cloud-functions/memoir-api`。

`memoir-api/config.json` 将云函数超时时间设置为 60 秒。修改配置或依赖后，需要重新选择 **上传并部署：云端安装依赖**。如果线上函数仍然显示 3 秒，需要在开发者工具或云开发控制台进入 `memoir-api` 函数配置，手动把超时时间改为 60 秒并保存。

部署后必须确认线上 timeout 也是 60 秒：

```bash
/Applications/wechatwebdevtools.app/Contents/MacOS/cli cloud functions info \
  --project /path/to/Pawtale \
  --env cloud1-d6gmxffp0ea48aa1a \
  --names memoir-api
```

云函数需要配置这些环境变量：

| Name | Required | Notes |
| --- | --- | --- |
| `STEPFUN_API_KEY` | Yes | StepFun TTS API key |
| `STEPFUN_BASE_URL` | No | Defaults to `https://api.stepfun.com` |
| `DEEPSEEK_API_KEY` | Yes | DeepSeek chat completion API key |
| `DEEPSEEK_MODEL` | No | Defaults to `deepseek-chat` |
| `DEEPSEEK_BASE_URL` | No | Defaults to `https://api.deepseek.com` |

## Smoke Test

部署后可以在微信开发者工具 Console 中验证云函数是否可达：

```js
wx.cloud.callFunction({
  name: "memoir-api",
  data: {
    method: "GET",
    path: "/api/mobile/memoir/health",
    data: {}
  }
}).then(console.log).catch(console.error)
```

期望返回：

```js
{
  result: {
    statusCode: 200,
    data: {
      ok: true,
      service: "memoir-api"
    }
  }
}
```
