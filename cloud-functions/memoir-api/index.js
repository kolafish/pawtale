const { createDeepSeekDraftService } = require("./src/ai/deepseek");
const cloud = require("wx-server-sdk");
const { createStepFunASRService } = require("./src/asr/stepfun");
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
      storage: {
        async downloadFile(fileID) {
          const result = await cloud.downloadFile({ fileID });
          return result.fileContent;
        },
      },
      asr: createStepFunASRService(),
      drafting: createDeepSeekDraftService(),
    },
  );
};
