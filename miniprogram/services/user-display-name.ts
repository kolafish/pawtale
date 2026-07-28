const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";

export function isProvisionalWeChatEmail(email: string): boolean {
  return email.trim().toLowerCase().endsWith("@wechat.local");
}

export function userDisplayName(displayName: string, email: string): string {
  const normalizedName = displayName.trim();
  if (normalizedName && normalizedName !== WECHAT_PLACEHOLDER_DISPLAY_NAME) {
    return normalizedName;
  }
  if (isProvisionalWeChatEmail(email)) {
    return "作者";
  }
  return email.split("@", 1)[0].trim() || "作者";
}
