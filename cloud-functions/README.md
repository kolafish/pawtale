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

云函数需要配置这些环境变量：

| Name | Required | Notes |
| --- | --- | --- |
| `STEPFUN_API_KEY` | Yes | StepFun ASR API key |
| `STEPFUN_ASR_MODEL` | No | Defaults to `stepaudio-2.5-asr` |
| `STEPFUN_BASE_URL` | No | Defaults to `https://api.stepfun.com` |
| `DEEPSEEK_API_KEY` | Yes | DeepSeek chat completion API key |
| `DEEPSEEK_MODEL` | No | Defaults to `deepseek-chat` |
| `DEEPSEEK_BASE_URL` | No | Defaults to `https://api.deepseek.com` |
