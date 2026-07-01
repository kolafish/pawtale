import { BACKEND_HTTP_BASE_URL, BACKEND_WS_BASE_URL } from "./config";
import { installAuditPageTracker } from "./services/audit-tracker";

installAuditPageTracker();

App({
  onLaunch() {},
  globalData: {
    backendHttpBaseUrl: BACKEND_HTTP_BASE_URL,
    backendWsBaseUrl: BACKEND_WS_BASE_URL,
  }
});
