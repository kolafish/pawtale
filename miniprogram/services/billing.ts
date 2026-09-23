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

export interface AIUsageDetail {
  id: string;
  purpose: string;
  cost_rmb: number | string;
  created_at: string;
}

export interface AIUsageDetailResponse {
  items: AIUsageDetail[];
  has_more: boolean;
}

export function getAICreditSummary(): Promise<AICreditSummary> {
  return memoirRequest<AICreditSummary>("/api/billing/ai-credit-summary");
}

export function getAIUsageDetails(offset: number): Promise<AIUsageDetailResponse> {
  return memoirRequest<AIUsageDetailResponse>(`/api/billing/ai-usage?offset=${offset}`);
}
