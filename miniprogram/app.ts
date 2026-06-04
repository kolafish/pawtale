import { API_TRANSPORT, CLOUD_ENV_ID, CLOUD_SERVICE_NAME, LOCAL_API_BASE_URL } from "./config";

App({
  onLaunch() {
    if (API_TRANSPORT === "cloud") {
      wx.cloud.init({
        env: CLOUD_ENV_ID || undefined,
      });
    }
  },
  globalData: {
    apiTransport: API_TRANSPORT,
    apiBaseUrl: LOCAL_API_BASE_URL,
    cloudEnvId: CLOUD_ENV_ID,
    cloudServiceName: CLOUD_SERVICE_NAME,
  }
});
