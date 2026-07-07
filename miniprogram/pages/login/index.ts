import {
  isValidEmail,
  loginWithEmail,
  loginWithWeChat,
} from "../../services/auth";
import { relaunchHome } from "../../services/navigation";
import { definePage } from "../../services/page-definition";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface LoginData {
  email: string;
  password: string;
  accountMode: boolean;
  loading: boolean;
  wechatLoading: boolean;
  error: string;
}

definePage<LoginData>()({
  data: {
    email: "",
    password: "",
    accountMode: false,
    loading: false,
    wechatLoading: false,
    error: "",
  },

  changeEmail(event: InputEvent) {
    this.setData({ email: event.detail.value, error: "" });
  },

  changePassword(event: InputEvent) {
    this.setData({ password: event.detail.value, error: "" });
  },

  toggleAccountMode() {
    this.setData({
      accountMode: !this.data.accountMode,
      error: "",
    });
  },

  async submitWeChat() {
    if (this.data.loading || this.data.wechatLoading) {
      return;
    }
    this.setData({ wechatLoading: true, error: "" });
    try {
      await loginWithWeChat();
      relaunchHome();
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "微信登录失败",
      });
    } finally {
      this.setData({ wechatLoading: false });
    }
  },

  async submitAccount() {
    if (this.data.loading || this.data.wechatLoading) {
      return;
    }
    const email = this.data.email.trim();
    const password = this.data.password;
    if (!email || !password) {
      this.setData({ error: "请输入邮箱和密码" });
      return;
    }
    if (!isValidEmail(email)) {
      this.setData({ error: "请输入正确的邮箱地址" });
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      await loginWithEmail({ email, password });
      relaunchHome();
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "登录失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },
});
