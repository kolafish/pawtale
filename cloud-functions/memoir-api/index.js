const {
  createDeepSeekDraftService,
  createDeepSeekMemoryExtractionService,
} = require("./src/ai/deepseek");
const cloud = require("wx-server-sdk");
const { createStepFunASRService } = require("./src/asr/stepfun");
const { createStepFunTTSService } = require("./src/tts/stepfun");
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
        async uploadFile(cloudPath, fileContent) {
          const result = await cloud.uploadFile({ cloudPath, fileContent });
          return result.fileID;
        },
      },
      asr: createStepFunASRService(),
      tts: createStepFunTTSService(),
      drafting: createDeepSeekDraftService(),
      memoryExtraction: createDeepSeekMemoryExtractionService(),
    },
  );
};
