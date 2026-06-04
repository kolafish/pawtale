export type ApiTransport = "local" | "cloudFunction";

export const MINIPROGRAM_APP_ID = "wx71acdf73e2ba7067";
export const CLOUD_ENV_ID = "cloud1-d6gmxffp0ea48aa1a";
export const CLOUD_FUNCTION_NAME = "memoir-api";

// Keep local as the default until the memoir cloud function is deployed.
export const API_TRANSPORT: ApiTransport = "local";

export const LOCAL_API_BASE_URL = "http://127.0.0.1:8000";
