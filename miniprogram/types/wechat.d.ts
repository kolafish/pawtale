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

  interface LoginSuccessCallbackResult extends GeneralCallbackResult {
    code: string;
  }

  interface LoginOption {
    timeout?: number;
    success?(result: LoginSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
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

  interface DownloadFileSuccessCallbackResult extends GeneralCallbackResult {
    tempFilePath: string;
    filePath?: string;
    statusCode: number;
    header: Record<string, string | string[] | undefined>;
  }

  interface DownloadFileOption {
    url: string;
    header?: Record<string, string>;
    timeout?: number;
    success?(result: DownloadFileSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface RecorderManagerStartOption {
    duration?: number;
    sampleRate?: number;
    numberOfChannels?: number;
    encodeBitRate?: number;
    format?: "mp3" | "aac" | "wav" | "PCM";
    frameSize?: number;
  }

  interface RecorderManagerStopCallbackResult {
    tempFilePath: string;
    duration: number;
    fileSize: number;
  }

  interface RecorderManagerFrameRecordedCallbackResult {
    frameBuffer: ArrayBuffer;
    isLastFrame: boolean;
  }

  interface RecorderManager {
    start(option?: RecorderManagerStartOption): void;
    stop(): void;
    onStart(callback: () => void): void;
    onStop(callback: (result: RecorderManagerStopCallbackResult) => void): void;
    onFrameRecorded(callback: (result: RecorderManagerFrameRecordedCallbackResult) => void): void;
    onError(callback: (result: GeneralCallbackResult) => void): void;
  }

  interface ConnectSocketOption {
    url: string;
    header?: Record<string, string>;
    protocols?: string[];
    tcpNoDelay?: boolean;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface SocketMessageCallbackResult {
    data: string | ArrayBuffer;
  }

  interface SocketTaskSendOption {
    data: string | ArrayBuffer;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface SocketTaskCloseOption {
    code?: number;
    reason?: string;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface SocketTask {
    send(option: SocketTaskSendOption): void;
    close(option?: SocketTaskCloseOption): void;
    onOpen(callback: (result: GeneralCallbackResult) => void): void;
    onMessage(callback: (result: SocketMessageCallbackResult) => void): void;
    onError(callback: (result: GeneralCallbackResult) => void): void;
    onClose(callback: (result: GeneralCallbackResult) => void): void;
  }

  interface InnerAudioContext {
    src: string;
    playbackRate: number;
    obeyMuteSwitch: boolean;
    volume: number;
    currentTime: number;
    duration: number;
    play(): void;
    pause(): void;
    stop(): void;
    destroy(): void;
    onPlay(callback: () => void): void;
    onPause(callback: () => void): void;
    onStop(callback: () => void): void;
    onTimeUpdate(callback: () => void): void;
    onEnded(callback: () => void): void;
    onError(callback: (error: GeneralCallbackResult) => void): void;
  }

  interface InnerAudioOption {
    obeyMuteSwitch?: boolean;
    mixWithOther?: boolean;
    speakerOn?: boolean;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface NavigateOption {
    url: string;
  }

  interface NavigateBackOption {
    delta?: number;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
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

  interface ModalOption {
    title?: string;
    content?: string;
    confirmText?: string;
    confirmColor?: string;
    cancelText?: string;
    cancelColor?: string;
    showCancel?: boolean;
    success?(result: { confirm: boolean; cancel: boolean; errMsg?: string }): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface ActionSheetOption {
    itemList: string[];
    itemColor?: string;
    success?(result: { tapIndex: number; errMsg?: string }): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface PrivacySettingOption {
    success?(result: { needAuthorization: boolean; privacyContractName?: string; errMsg?: string }): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface PrivacyAuthorizeOption {
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface Wx {
    login(option?: LoginOption): void;
    request<T = unknown>(option: RequestOption<T>): void;
    downloadFile(option: DownloadFileOption): void;
    connectSocket(option: ConnectSocketOption): SocketTask;
    getRecorderManager(): RecorderManager;
    createInnerAudioContext(): InnerAudioContext;
    getPrivacySetting?(option: PrivacySettingOption): void;
    requirePrivacyAuthorize?(option: PrivacyAuthorizeOption): void;
    setInnerAudioOption?(option: InnerAudioOption): void;
    navigateTo(option: NavigateOption): void;
    redirectTo(option: NavigateOption): void;
    reLaunch(option: NavigateOption): void;
    switchTab(option: NavigateOption): void;
    navigateBack(option?: NavigateBackOption): void;
    showActionSheet(option: ActionSheetOption): void;
    showModal(option: ModalOption): void;
    showToast(option: ToastOption): void;
    showLoading(option: LoadingOption): void;
    hideLoading(): void;
    getStorageSync<T = unknown>(key: string): T;
    setStorageSync<T = unknown>(key: string, value: T): void;
    removeStorageSync(key: string): void;
  }
}
