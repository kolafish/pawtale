import { currentSessionCookie } from "../../services/auth";

Page({
  data: {},

  onLoad() {
    wx.reLaunch({
      url: currentSessionCookie() ? "/pages/home/index" : "/pages/login/index",
    });
  },
});
