const fs = require("node:fs/promises");

const ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf";

class AppiumCommandError extends Error {
  constructor(command, payload) {
    const value = payload?.value || {};
    super(String(value.message || `Appium 命令失败：${command}`));
    this.name = "AppiumCommandError";
    this.command = command;
    this.appiumError = value.error || "unknown error";
  }
}

class AppiumClient {
  constructor(serverUrl, options = {}) {
    this.serverUrl = String(serverUrl).replace(/\/$/, "");
    this.fetch = options.fetch || globalThis.fetch;
    this.sessionId = options.sessionId || null;
    this.capabilities = null;
  }

  async request(path, options = {}) {
    const method = options.method || "GET";
    const timeoutMs = options.timeoutMs || 30000;
    let response;
    try {
      response = await this.fetch(`${this.serverUrl}${path}`, {
        method,
        headers: options.body ? { "content-type": "application/json" } : undefined,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      if (error.name === "TimeoutError") {
        throw new Error(`Appium ${method} ${path} 在 ${timeoutMs}ms 内未返回`);
      }
      throw error;
    }
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error(`Appium ${method} ${path} 返回了无效 JSON`);
    }
    if (!response.ok || payload?.value?.error) {
      throw new AppiumCommandError(`${method} ${path}`, payload);
    }
    return payload;
  }

  sessionPath(path = "") {
    if (!this.sessionId) {
      throw new Error("Appium 会话尚未创建");
    }
    return `/session/${encodeURIComponent(this.sessionId)}${path}`;
  }

  async status() {
    return (await this.request("/status", { timeoutMs: 5000 })).value;
  }

  async createSession(alwaysMatch) {
    const payload = await this.request("/session", {
      method: "POST",
      timeoutMs: 3 * 60 * 1000,
      body: {
        capabilities: {
          alwaysMatch,
          firstMatch: [{}],
        },
      },
    });
    this.sessionId = payload.sessionId || payload.value?.sessionId;
    this.capabilities = payload.value?.capabilities || payload.value || {};
    if (!this.sessionId) {
      throw new Error("Appium 创建会话后未返回 session id");
    }
    return this.capabilities;
  }

  async deleteSession() {
    if (!this.sessionId) {
      return;
    }
    const sessionId = this.sessionId;
    this.sessionId = null;
    await this.request(`/session/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
      timeoutMs: 30000,
    });
  }

  async source() {
    return (await this.request(this.sessionPath("/source"))).value;
  }

  async windowRect() {
    return (await this.request(this.sessionPath("/window/rect"))).value;
  }

  async screenshot(filePath) {
    const encoded = (await this.request(this.sessionPath("/screenshot"))).value;
    await fs.writeFile(filePath, Buffer.from(encoded, "base64"));
  }

  async activateApp(bundleId) {
    await this.request(this.sessionPath("/appium/device/activate_app"), {
      method: "POST",
      body: { bundleId },
    });
  }

  async findElements(using, value) {
    return (await this.request(this.sessionPath("/elements"), {
      method: "POST",
      body: { using, value },
    })).value;
  }

  async findElement(using, value) {
    return (await this.request(this.sessionPath("/element"), {
      method: "POST",
      body: { using, value },
    })).value;
  }

  elementId(element) {
    const id = element?.[ELEMENT_KEY] || element?.ELEMENT;
    if (!id) {
      throw new Error("Appium 元素缺少 element id");
    }
    return id;
  }

  elementPath(element, suffix = "") {
    return this.sessionPath(
      `/element/${encodeURIComponent(this.elementId(element))}${suffix}`,
    );
  }

  async elementRect(element) {
    return (await this.request(this.elementPath(element, "/rect"))).value;
  }

  async elementAttribute(element, name) {
    return (await this.request(
      this.elementPath(element, `/attribute/${encodeURIComponent(name)}`),
    )).value;
  }

  async clickElement(element) {
    await this.request(this.elementPath(element, "/click"), {
      method: "POST",
      body: {},
    });
  }

  async clearElement(element) {
    await this.request(this.elementPath(element, "/clear"), {
      method: "POST",
      body: {},
    });
  }

  async typeElement(element, text) {
    await this.request(this.elementPath(element, "/value"), {
      method: "POST",
      body: { text, value: [text] },
    });
  }

  async typeKeys(text) {
    await this.request(this.sessionPath("/keys"), {
      method: "POST",
      body: { value: [text] },
    });
  }

  async tap(x, y, pauseMs = 120) {
    await this.request(this.sessionPath("/actions"), {
      method: "POST",
      body: {
        actions: [{
          type: "pointer",
          id: "finger1",
          parameters: { pointerType: "touch" },
          actions: [
            { type: "pointerMove", duration: 0, x: Math.round(x), y: Math.round(y) },
            { type: "pointerDown", button: 0 },
            { type: "pause", duration: pauseMs },
            { type: "pointerUp", button: 0 },
          ],
        }],
      },
    });
  }

  async tapElementCenter(element) {
    const rect = await this.elementRect(element);
    await this.tap(rect.x + rect.width / 2, rect.y + rect.height / 2);
  }
}

module.exports = {
  AppiumClient,
  AppiumCommandError,
  ELEMENT_KEY,
};
