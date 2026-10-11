import { wxLoginCode } from "./auth";
import { memoirRequest } from "./memoir-api-client";

export interface AICreditSummary {
  currency: string;
  free_credit_rmb: number;
  manual_grant_rmb: number;
  recharged_credit_rmb: number;
  total_credit_rmb: number;
  used_rmb: number;
  remaining_rmb: number;
  unpriced_success_count: number;
}

export interface AIUsagePurposeTotal {
  purpose: string;
  count: number;
  cost_rmb: number | string;
}

export interface AICreditEntry {
  id: string;
  kind: string;
  label: string;
  amount_rmb: number | string;
  note: string;
  created_at: string;
}

/** One Beijing-time day of AI charges and credit additions. */
export interface AIUsageDay {
  day: string;
  cost_rmb: number | string;
  purposes: AIUsagePurposeTotal[];
  credits: AICreditEntry[];
}

export interface AIUsageDetailResponse {
  days: AIUsageDay[];
  has_more: boolean;
}

export function getAICreditSummary(): Promise<AICreditSummary> {
  return memoirRequest<AICreditSummary>("/api/billing/ai-credit-summary");
}

/** `offset` counts days already loaded. */
export function getAIUsageDetails(offset: number): Promise<AIUsageDetailResponse> {
  return memoirRequest<AIUsageDetailResponse>(`/api/billing/ai-usage?offset=${offset}`);
}

export interface CreditPack {
  product_id: string;
  name: string;
  price_rmb: number | string;
  credit_rmb: number | string;
}

export interface CreditPacks {
  enabled: boolean;
  packs: CreditPack[];
}

interface CreditOrderPayment {
  order_no: string;
  mode: string;
  sign_data: string;
  pay_sig: string;
  signature: string;
}

interface CreditOrderStatus {
  order_no: string;
  status: "pending" | "paid" | "refunded";
  credit_rmb: number | string;
}

export type CreditPurchaseResult = "paid" | "pending" | "cancelled";

const IOS_MIN_WECHAT_VERSION = [8, 0, 68];
const IOS_MIN_SYSTEM_MAJOR = 15;
const ORDER_POLL_ATTEMPTS = 10;
const ORDER_POLL_INTERVAL_MS = 1500;

export function getCreditPacks(): Promise<CreditPacks> {
  return memoirRequest<CreditPacks>("/api/billing/credit-packs");
}

function versionAtLeast(version: string, minimum: number[]): boolean {
  const parts = version.split(".").map((part) => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < minimum.length; index += 1) {
    const part = parts[index] ?? 0;
    if (part !== minimum[index]) {
      return part > minimum[index];
    }
  }
  return true;
}

/** Apple payments need WeChat 8.0.68+ on iOS 15+; returns the reason when this phone cannot pay. */
export function creditPurchaseBlockedReason(): string {
  if (!wx.requestVirtualPayment) {
    return "当前微信版本不支持充值，请升级微信后再试";
  }
  const device = wx.getDeviceInfo?.();
  if (device?.platform !== "ios") {
    return "";
  }
  const systemMajor = Number.parseInt(device.system.replace(/^\D+/, ""), 10) || 0;
  const wechatVersion = wx.getAppBaseInfo?.().version || "";
  if (systemMajor < IOS_MIN_SYSTEM_MAJOR || !versionAtLeast(wechatVersion, IOS_MIN_WECHAT_VERSION)) {
    return "iPhone 充值需要微信 8.0.68 及以上、iOS 15 及以上，请升级后再试";
  }
  return "";
}

function requestVirtualPayment(payment: CreditOrderPayment): Promise<boolean> {
  return new Promise((resolve, reject) => {
    wx.requestVirtualPayment!({
      signData: payment.sign_data,
      paySig: payment.pay_sig,
      signature: payment.signature,
      mode: payment.mode,
      success: () => resolve(true),
      fail(error) {
        if (/cancel/i.test(error.errMsg || "")) {
          resolve(false);
          return;
        }
        reject(new Error(error.errMsg || "支付失败"));
      },
    });
  });
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Pay for one pack; credit arrives through WeChat's delivery push, so poll the order afterwards. */
export async function buyCreditPack(productId: string): Promise<CreditPurchaseResult> {
  const code = await wxLoginCode();
  const payment = await memoirRequest<CreditOrderPayment>("/api/billing/credit-orders", {
    method: "POST",
    data: { product_id: productId, code },
    invalidatesSnapshots: false,
  });
  if (!(await requestVirtualPayment(payment))) {
    return "cancelled";
  }
  for (let attempt = 0; attempt < ORDER_POLL_ATTEMPTS; attempt += 1) {
    const order = await memoirRequest<CreditOrderStatus>(`/api/billing/credit-orders/${payment.order_no}`);
    if (order.status !== "pending") {
      return "paid";
    }
    await wait(ORDER_POLL_INTERVAL_MS);
  }
  return "pending";
}
