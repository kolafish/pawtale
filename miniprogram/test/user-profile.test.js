require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, mockModule } = require("./page-harness");

function loadUserProfile(uploadAuthenticatedFile) {
  mockModule("../services/authenticated-media.ts", { uploadAuthenticatedFile });
  mockModule("../services/request.ts", {
    cleanBaseUrl(value) {
      return value.replace(/\/$/, "");
    },
    readBackendConfig() {
      return { httpBaseUrl: "https://api.example.com/" };
    },
  });
  clearModule("../services/user-profile.ts");
  return require("../services/user-profile.ts");
}

test("resolveBackendAvatarUrl resolves backend-owned avatar paths", () => {
  const { resolveBackendAvatarUrl } = loadUserProfile(async () => ({}));

  assert.equal(
    resolveBackendAvatarUrl("/api/auth/avatars/user/avatar.jpg"),
    "https://api.example.com/api/auth/avatars/user/avatar.jpg",
  );
  assert.equal(
    resolveBackendAvatarUrl("https://cdn.example.com/avatar.jpg"),
    "https://cdn.example.com/avatar.jpg",
  );
  assert.equal(resolveBackendAvatarUrl(null), "");
});

test("uploadBackendAvatar posts the selected file and parses the user", async () => {
  const calls = [];
  const expectedUser = {
    id: "user-1",
    email: "wechat_x@wechat.local",
    password_login_enabled: false,
    display_name: "微信昵称",
    avatar_url: "/api/auth/avatars/user-1/avatar.jpg",
    created_at: "2026-07-24T00:00:00",
  };
  const { uploadBackendAvatar } = loadUserProfile(async (url, options) => {
    calls.push({ url, options });
    return options.parseResponse(JSON.stringify(expectedUser));
  });

  const user = await uploadBackendAvatar("wxfile://avatar");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.example.com/api/auth/me/avatar");
  assert.equal(calls[0].options.filePath, "wxfile://avatar");
  assert.equal(calls[0].options.name, "file");
  assert.deepEqual(user, expectedUser);
});

test("uploadBackendAvatar rejects malformed backend responses", async () => {
  const { uploadBackendAvatar } = loadUserProfile(async (_url, options) => (
    options.parseResponse("{}")
  ));

  await assert.rejects(
    uploadBackendAvatar("wxfile://avatar"),
    /头像保存响应格式错误/,
  );
});
