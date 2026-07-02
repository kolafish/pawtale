export type PageQuery = Record<string, string | undefined>;

export interface PageDatasetEvent<TDataset = Record<string, unknown>> {
  currentTarget: {
    dataset: TDataset;
  };
}

export interface PagePickerEvent<TDataset = Record<string, unknown>> extends PageDatasetEvent<TDataset> {
  detail: {
    value: string | number;
  };
}

export interface PageTextInputEvent<TDataset = Record<string, unknown>> extends PageDatasetEvent<TDataset> {
  detail: {
    value: string;
  };
}

export interface PageSwitchEvent<TDataset = Record<string, unknown>> extends PageDatasetEvent<TDataset> {
  detail: {
    value: boolean;
  };
}

type PageUrlParam = string | number | boolean | null | undefined;

export function safeDecodeURIComponent(value = ""): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function queryParam(query: PageQuery | undefined, key: string): string {
  return safeDecodeURIComponent(query?.[key] || "");
}

export function queryChoice<TValue extends string>(
  query: PageQuery | undefined,
  key: string,
  allowedValues: readonly TValue[],
  fallback: TValue,
): TValue {
  const value = queryParam(query, key) as TValue;
  return allowedValues.includes(value) ? value : fallback;
}

export function datasetString<TDataset, TKey extends keyof TDataset>(
  event: PageDatasetEvent<TDataset>,
  key: TKey,
): string {
  const value = event.currentTarget.dataset[key];
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

export function datasetChoice<TDataset, TKey extends keyof TDataset, TValue extends string>(
  event: PageDatasetEvent<TDataset>,
  key: TKey,
  allowedValues: readonly TValue[],
  fallback: TValue,
): TValue {
  const value = datasetString(event, key) as TValue;
  return allowedValues.includes(value) ? value : fallback;
}

export function datasetNumber<TDataset, TKey extends keyof TDataset>(
  event: PageDatasetEvent<TDataset>,
  key: TKey,
  fallback = 0,
): number {
  const rawValue = datasetString(event, key).trim();
  if (!rawValue) {
    return fallback;
  }
  const value = Number(rawValue);
  return Number.isFinite(value) ? value : fallback;
}

export function pickerIndex<TDataset>(event: PagePickerEvent<TDataset>): number {
  if (typeof event.detail.value === "string" && !event.detail.value.trim()) {
    return -1;
  }
  const index = Number(event.detail.value);
  return Number.isInteger(index) && index >= 0 ? index : -1;
}

export function pageUrl(path: string, params: Record<string, PageUrlParam> = {}): string {
  const query = Object.entries(params)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join("&");
  return query ? `${path}?${query}` : path;
}
