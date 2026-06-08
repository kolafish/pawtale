import { API_TRANSPORT, CLOUD_ENV_ID, CLOUD_FUNCTION_NAME, LOCAL_API_BASE_URL } from "./config";

App({
  onLaunch() {
    if (API_TRANSPORT === "cloudFunction") {
      wx.cloud.init({
        env: CLOUD_ENV_ID,
        traceUser: true,
      });
    }
  },
  globalData: {
    apiTransport: API_TRANSPORT,
    apiBaseUrl: LOCAL_API_BASE_URL,
    cloudEnvId: CLOUD_ENV_ID,
    cloudFunctionName: CLOUD_FUNCTION_NAME,
  }
});
