import { loginWithEmail, registerWithEmail } from "../../services/auth";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface LoginData {
  email: string;
  password: string;
  displayName: string;
  registerMode: boolean;
  loading: boolean;
  error: string;
}

Page<LoginData, {
  changeEmail(event: InputEvent): void;
  changePassword(event: InputEvent): void;
  changeDisplayName(event: InputEvent): void;
  toggleMode(): void;
  submit(): Promise<void>;
}>({
  data: {
    email: "",
    password: "",
    displayName: "",
    registerMode: false,
    loading: false,
    error: "",
  },

  changeEmail(event) {
    this.setData({ email: event.detail.value, error: "" });
  },

  changePassword(event) {
    this.setData({ password: event.detail.value, error: "" });
  },

  changeDisplayName(event) {
    this.setData({ displayName: event.detail.value, error: "" });
  },

  toggleMode() {
    this.setData({
      registerMode: !this.data.registerMode,
      error: "",
    });
  },

  async submit() {
    if (this.data.loading) {
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
      if (this.data.registerMode) {
        await registerWithEmail({
          email,
          password,
          display_name: this.data.displayName.trim() || undefined,
        });
      } else {
        await loginWithEmail({ email, password });
      }
      wx.reLaunch({ url: "/pages/home/index" });
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "登录失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },
});
