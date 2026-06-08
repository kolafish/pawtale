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

  interface UploadFileSuccessCallbackResult {
    data: string;
    statusCode: number;
    header: Record<string, string | string[] | undefined>;
    cookies?: string[];
  }

  interface UploadFileOption {
    url: string;
    filePath: string;
    name: string;
    header?: Record<string, string>;
    formData?: Record<string, string>;
    timeout?: number;
    success?(result: UploadFileSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface GetFileInfoSuccessCallbackResult {
    size: number;
    digest?: string;
    errMsg?: string;
  }

  interface GetFileInfoOption {
    filePath: string;
    digestAlgorithm?: "md5" | "sha1";
    success?(result: GetFileInfoSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface UserInfo {
    nickName?: string;
    avatarUrl?: string;
  }

  interface GetUserProfileSuccessCallbackResult extends GeneralCallbackResult {
    userInfo: UserInfo;
  }

  interface GetUserProfileOption {
    desc: string;
    success?(result: GetUserProfileSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface LoginSuccessCallbackResult extends GeneralCallbackResult {
    code: string;
  }

  interface LoginOption {
    timeout?: number;
    success?(result: LoginSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface CloudInitOption {
    env?: string;
    traceUser?: boolean;
  }

  interface CloudCallFunctionResult<T = unknown> {
    result: T;
    errMsg?: string;
  }

  interface CloudCallFunctionOption<T = unknown> {
    name: string;
    data?: unknown;
    config?: CloudInitOption;
    success?(result: CloudCallFunctionResult<T>): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface CloudUploadFileResult {
    fileID: string;
    statusCode?: number;
    errMsg?: string;
  }

  interface CloudUploadFileOption {
    cloudPath: string;
    filePath: string;
    config?: CloudInitOption;
    success?(result: CloudUploadFileResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface Cloud {
    init(option?: CloudInitOption): void;
    callFunction<T = unknown>(option: CloudCallFunctionOption<T>): Promise<CloudCallFunctionResult<T>>;
    uploadFile(option: CloudUploadFileOption): Promise<CloudUploadFileResult>;
  }

  interface RecorderManagerStartOption {
    duration?: number;
    sampleRate?: number;
    numberOfChannels?: number;
    encodeBitRate?: number;
    format?: "mp3" | "aac" | "wav";
    frameSize?: number;
  }

  interface RecorderManagerStopCallbackResult {
    tempFilePath: string;
    duration: number;
    fileSize: number;
  }

  interface RecorderManager {
    start(option?: RecorderManagerStartOption): void;
    stop(): void;
    onStart(callback: () => void): void;
    onStop(callback: (result: RecorderManagerStopCallbackResult) => void): void;
    onError(callback: (result: GeneralCallbackResult) => void): void;
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
    cloud: Cloud;
    request<T = unknown>(option: RequestOption<T>): void;
    uploadFile(option: UploadFileOption): void;
    login(option?: LoginOption): void;
    getUserProfile(option: GetUserProfileOption): void;
    getFileInfo(option: GetFileInfoOption): void;
    getRecorderManager(): RecorderManager;
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
