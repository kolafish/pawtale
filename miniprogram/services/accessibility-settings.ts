export interface AccessibilitySettings {
  largeTextEnabled: boolean;
  autoBargeInEnabled: boolean;
}

const STORAGE_KEY = "pawtale_accessibility_settings";

export const DEFAULT_ACCESSIBILITY_SETTINGS: AccessibilitySettings = {
  largeTextEnabled: false,
  autoBargeInEnabled: true,
};

function normalizeBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function normalizeAccessibilitySettings(value: unknown): AccessibilitySettings {
  if (!value || typeof value !== "object") {
    return { ...DEFAULT_ACCESSIBILITY_SETTINGS };
  }
  const candidate = value as Partial<AccessibilitySettings>;
  return {
    largeTextEnabled: normalizeBoolean(
      candidate.largeTextEnabled,
      DEFAULT_ACCESSIBILITY_SETTINGS.largeTextEnabled,
    ),
    autoBargeInEnabled: normalizeBoolean(
      candidate.autoBargeInEnabled,
      DEFAULT_ACCESSIBILITY_SETTINGS.autoBargeInEnabled,
    ),
  };
}

function canUseStorage(): boolean {
  return typeof wx !== "undefined"
    && typeof wx.getStorageSync === "function"
    && typeof wx.setStorageSync === "function";
}

export function readAccessibilitySettings(): AccessibilitySettings {
  if (!canUseStorage()) {
    return { ...DEFAULT_ACCESSIBILITY_SETTINGS };
  }
  try {
    return normalizeAccessibilitySettings(wx.getStorageSync<AccessibilitySettings>(STORAGE_KEY));
  } catch (error) {
    console.warn("read accessibility settings failed", error);
    return { ...DEFAULT_ACCESSIBILITY_SETTINGS };
  }
}

export function saveAccessibilitySettings(
  patch: Partial<AccessibilitySettings>,
): AccessibilitySettings {
  const settings = normalizeAccessibilitySettings({
    ...readAccessibilitySettings(),
    ...patch,
  });
  if (canUseStorage()) {
    wx.setStorageSync(STORAGE_KEY, settings);
  }
  return settings;
}

export function readLargeTextEnabled(): boolean {
  return readAccessibilitySettings().largeTextEnabled;
}

export function saveLargeTextEnabled(largeTextEnabled: boolean): AccessibilitySettings {
  return saveAccessibilitySettings({ largeTextEnabled });
}

export function readAutoBargeInEnabled(): boolean {
  return readAccessibilitySettings().autoBargeInEnabled;
}

export function saveAutoBargeInEnabled(autoBargeInEnabled: boolean): AccessibilitySettings {
  return saveAccessibilitySettings({ autoBargeInEnabled });
}
