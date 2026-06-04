const cloud = require("wx-server-sdk");
const { handleMemoirApiRequest } = require("./src/router");
const { CloudStore } = require("./src/store");

cloud.init({
  env: cloud.DYNAMIC_CURRENT_ENV,
});

exports.main = async (event, context) => {
  const wxContext = cloud.getWXContext();
  return handleMemoirApiRequest(
    event,
    {
      ...context,
      openid: wxContext.OPENID,
      appid: wxContext.APPID,
      unionid: wxContext.UNIONID,
    },
    {
      store: new CloudStore(cloud.database()),
    },
  );
};
