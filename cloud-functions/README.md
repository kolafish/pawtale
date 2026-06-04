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

## Local Checks

```bash
node --test cloud-functions/memoir-api/test/*.test.js
```

## Deploy

用微信开发者工具打开 `miniprogram/` 后，在云开发面板中选择环境 `cloud1`，上传并部署 `cloud-functions/memoir-api`。
