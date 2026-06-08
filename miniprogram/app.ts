import { CLOUD_ENV_ID, CLOUD_FUNCTION_NAME } from "./config";

App({
  onLaunch() {
    wx.cloud.init({
      env: CLOUD_ENV_ID,
      traceUser: true,
    });
  },
  globalData: {
    cloudEnvId: CLOUD_ENV_ID,
    cloudFunctionName: CLOUD_FUNCTION_NAME,
  }
});
