import {
  loginWithEmail,
  loginWithWeChat,
  setAccountCredentials,
} from "../../services/auth";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface LoginData {
  email: string;
  password: string;
  credentialEmail: string;
  credentialPassword: string;
  credentialDisplayName: string;
  accountMode: boolean;
  credentialsMode: boolean;
  loading: boolean;
  wechatLoading: boolean;
  credentialsLoading: boolean;
  error: string;
}

Page<LoginData, {
  changeEmail(event: InputEvent): void;
  changePassword(event: InputEvent): void;
  changeCredentialEmail(event: InputEvent): void;
  changeCredentialPassword(event: InputEvent): void;
  changeCredentialDisplayName(event: InputEvent): void;
  toggleAccountMode(): void;
  submitWeChat(): Promise<void>;
  submitAccount(): Promise<void>;
  submitCredentials(): Promise<void>;
}>({
  data: {
    email: "",
    password: "",
    credentialEmail: "",
    credentialPassword: "",
    credentialDisplayName: "",
    accountMode: false,
    credentialsMode: false,
    loading: false,
    wechatLoading: false,
    credentialsLoading: false,
    error: "",
  },

  changeEmail(event) {
    this.setData({ email: event.detail.value, error: "" });
  },

  changePassword(event) {
    this.setData({ password: event.detail.value, error: "" });
  },

  changeCredentialEmail(event) {
    this.setData({ credentialEmail: event.detail.value, error: "" });
  },

  changeCredentialPassword(event) {
    this.setData({ credentialPassword: event.detail.value, error: "" });
  },

  changeCredentialDisplayName(event) {
    this.setData({ credentialDisplayName: event.detail.value, error: "" });
  },

  toggleAccountMode() {
    this.setData({
      accountMode: !this.data.accountMode,
      error: "",
    });
  },

  async submitWeChat() {
    if (this.data.loading || this.data.wechatLoading || this.data.credentialsLoading) {
      return;
    }
    this.setData({ wechatLoading: true, error: "" });
    try {
      const response = await loginWithWeChat();
      if (response.needs_credentials || !response.user.password_login_enabled) {
        this.setData({
          credentialsMode: true,
          accountMode: false,
          credentialDisplayName: response.user.display_name === "微信用户"
            ? ""
            : response.user.display_name,
          error: "",
        });
        return;
      }
      wx.reLaunch({ url: "/pages/home/index" });
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "微信登录失败",
      });
    } finally {
      this.setData({ wechatLoading: false });
    }
  },

  async submitAccount() {
    if (this.data.loading || this.data.wechatLoading || this.data.credentialsLoading) {
      return;
    }
    const email = this.data.email.trim();
    const password = this.data.password;
    if (!email || !password) {
      this.setData({ error: "请输入邮箱和密码" });
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      await loginWithEmail({ email, password });
      wx.reLaunch({ url: "/pages/home/index" });
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "登录失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  async submitCredentials() {
    if (this.data.loading || this.data.wechatLoading || this.data.credentialsLoading) {
      return;
    }
    const email = this.data.credentialEmail.trim();
    const password = this.data.credentialPassword;
    const displayName = this.data.credentialDisplayName.trim();
    if (!email || !password) {
      this.setData({ error: "请输入邮箱和密码" });
      return;
    }
    if (password.length < 8) {
      this.setData({ error: "密码至少需要 8 位" });
      return;
    }
    this.setData({ credentialsLoading: true, error: "" });
    try {
      const response = await setAccountCredentials({
        email,
        password,
        display_name: displayName || undefined,
      });
      if (response.needs_credentials) {
        this.setData({ error: "账号还没有设置完成，请重新提交" });
        return;
      }
      wx.reLaunch({ url: "/pages/home/index" });
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "保存账号失败",
      });
    } finally {
      this.setData({ credentialsLoading: false });
    }
  },
});
