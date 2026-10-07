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
