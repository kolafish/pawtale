import { BACKEND_HTTP_BASE_URL, BACKEND_WS_BASE_URL } from "./config";
import {
  installAuditMemoryWarningTracker,
  installAuditPageTracker,
} from "./services/audit-tracker";
import {
  recoverMissingPage,
  type PageNotFoundQuery,
} from "./services/navigation";

interface PageNotFoundOptions {
  path: string;
  query: PageNotFoundQuery;
}

installAuditPageTracker();
installAuditMemoryWarningTracker();

App({
  onPageNotFound(options: PageNotFoundOptions) {
    recoverMissingPage(options.path, options.query);
  },
  globalData: {
    backendHttpBaseUrl: BACKEND_HTTP_BASE_URL,
    backendWsBaseUrl: BACKEND_WS_BASE_URL,
  }
});
