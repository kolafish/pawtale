export type ApiTransport = "local" | "cloud";

export const MINIPROGRAM_APP_ID = "wx71acdf73e2ba7067";

// Keep local as the default until the CloudBase environment and service are created.
export const API_TRANSPORT: ApiTransport = "local";

export const LOCAL_API_BASE_URL = "http://127.0.0.1:8000";
export const CLOUD_ENV_ID = "";
export const CLOUD_SERVICE_NAME = "pawtale-api";
