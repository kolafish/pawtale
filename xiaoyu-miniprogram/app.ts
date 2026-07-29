import { BACKEND_HTTP_BASE_URL, BACKEND_WS_BASE_URL } from "./config";


App({
  globalData: {
    backendHttpBaseUrl: BACKEND_HTTP_BASE_URL,
    backendWsBaseUrl: BACKEND_WS_BASE_URL,
  },
});
