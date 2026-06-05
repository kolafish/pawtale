import type { User } from "../domain/memoir";
import { API_TRANSPORT } from "../config";
import { ApiError, request } from "./request";

interface AuthResponse {
  user: User;
}

const DEV_USER = {
  email: "miniprogram-dev@example.com",
  password: "password123",
  display_name: "小程序测试用户",
};

export async function ensureLocalDevSession(): Promise<User> {
  if (API_TRANSPORT === "cloudFunction") {
    return request<User>("/api/mobile/memoir/me");
  }

  try {
    return await request<User>("/api/auth/me");
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 401) {
      throw error;
    }
  }

  try {
    const registered = await request<AuthResponse>("/api/auth/register", {
      method: "POST",
      data: DEV_USER,
    });
    return registered.user;
  } catch (error) {
    if (!(error instanceof ApiError) || error.statusCode !== 409) {
      throw error;
    }
  }

  const loggedIn = await request<AuthResponse>("/api/auth/login", {
    method: "POST",
    data: {
      email: DEV_USER.email,
      password: DEV_USER.password,
    },
  });
  return loggedIn.user;
}
