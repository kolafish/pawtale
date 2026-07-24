import type { BackendAuthUser } from "./auth";
import { uploadAuthenticatedFile } from "./authenticated-media";
import { cleanBaseUrl, readBackendConfig } from "./request";

function profileResponse(data: string): BackendAuthUser {
  try {
    const user = JSON.parse(data) as Partial<BackendAuthUser>;
    if (
      !user
      || typeof user.id !== "string"
      || typeof user.display_name !== "string"
      || typeof user.email !== "string"
    ) {
      throw new Error("invalid profile");
    }
    return user as BackendAuthUser;
  } catch {
    throw new Error("头像保存响应格式错误");
  }
}

function profileErrorMessage(data: string): string {
  try {
    const payload = JSON.parse(data) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : "头像保存失败";
  } catch {
    return "头像保存失败";
  }
}

export function resolveBackendAvatarUrl(avatarUrl: string | null | undefined): string {
  const normalized = String(avatarUrl || "").trim();
  if (!normalized || /^https?:\/\//i.test(normalized) || !normalized.startsWith("/")) {
    return normalized;
  }
  return `${cleanBaseUrl(readBackendConfig().httpBaseUrl)}${normalized}`;
}

export async function uploadBackendAvatar(filePath: string): Promise<BackendAuthUser> {
  const config = readBackendConfig();
  return uploadAuthenticatedFile(
    `${cleanBaseUrl(config.httpBaseUrl)}/api/auth/me/avatar`,
    {
      filePath,
      name: "file",
      domainLabel: "头像上传",
      failureMessage: "头像保存失败",
      responseErrorMessage: profileErrorMessage,
      parseResponse: profileResponse,
    },
  );
}
