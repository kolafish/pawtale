declare const wx: WechatMiniprogram.Wx;

declare function App<T extends Record<string, unknown>>(options: T): void;

declare function Page<TData extends object, TMethods extends object>(
  options: TMethods & {
    data: TData;
    onLoad?(query?: Record<string, string | undefined>): void | Promise<void>;
    onShow?(): void | Promise<void>;
  } & ThisType<TMethods & { data: TData; setData(data: Partial<TData>): void }>
): void;

declare namespace WechatMiniprogram {
  type RequestMethod = "OPTIONS" | "GET" | "HEAD" | "POST" | "PUT" | "DELETE" | "TRACE" | "CONNECT" | "PATCH";

  interface GeneralCallbackResult {
    errMsg: string;
  }

  interface RequestSuccessCallbackResult<T = unknown> {
    data: T;
    statusCode: number;
    header: Record<string, string | string[] | undefined>;
    cookies?: string[];
  }

  interface RequestOption<T = unknown> {
    url: string;
    data?: unknown;
    header?: Record<string, string>;
    method?: RequestMethod;
    timeout?: number;
    success?(result: RequestSuccessCallbackResult<T>): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface NavigateOption {
    url: string;
  }

  interface ToastOption {
    title: string;
    icon?: "success" | "error" | "loading" | "none";
    duration?: number;
  }

  interface LoadingOption {
    title: string;
    mask?: boolean;
  }

  interface Wx {
    request<T = unknown>(option: RequestOption<T>): void;
    navigateTo(option: NavigateOption): void;
    redirectTo(option: NavigateOption): void;
    reLaunch(option: NavigateOption): void;
    showToast(option: ToastOption): void;
    showLoading(option: LoadingOption): void;
    hideLoading(): void;
    getStorageSync<T = unknown>(key: string): T;
    setStorageSync<T = unknown>(key: string, value: T): void;
    removeStorageSync(key: string): void;
  }
}
