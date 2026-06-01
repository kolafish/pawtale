function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function getApiErrorDetail(error: unknown): unknown {
  if (!isRecord(error)) return undefined;
  const response = error.response;
  if (!isRecord(response)) return undefined;
  const data = response.data;
  if (!isRecord(data)) return undefined;
  return data.detail;
}

export function getErrorMessage(error: unknown, fallback: string): string {
  const detail = getApiErrorDetail(error);
  if (typeof detail === 'string' && detail) return detail;
  if (isRecord(detail) && typeof detail.message === 'string') return detail.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function getErrorDetailRecord(error: unknown): Record<string, unknown> | null {
  const detail = getApiErrorDetail(error);
  return isRecord(detail) ? detail : null;
}
