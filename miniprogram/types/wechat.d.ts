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

  interface FileSystemReadFileSuccessCallbackResult extends GeneralCallbackResult {
    data: string | ArrayBuffer;
  }

  interface FileSystemReadFileOption {
    filePath: string;
    encoding?: "ascii" | "base64" | "binary" | "hex" | "ucs2" | "ucs-2" | "utf16le" | "utf-16le" | "utf-8" | "utf8" | "latin1";
    success?(result: FileSystemReadFileSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface FileSystemWriteFileOption {
    filePath: string;
    data: string | ArrayBuffer;
    encoding?: "ascii" | "base64" | "binary" | "hex" | "ucs2" | "ucs-2" | "utf16le" | "utf-16le" | "utf-8" | "utf8" | "latin1";
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface FileSystemUnlinkOption {
    filePath: string;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface FileSystemManager {
    readFile(option: FileSystemReadFileOption): void;
    writeFile(option: FileSystemWriteFileOption): void;
    unlink?(option: FileSystemUnlinkOption): void;
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

  interface AudioBuffer {
    readonly sampleRate: number;
    readonly length: number;
    readonly duration: number;
    readonly numberOfChannels: number;
    getChannelData(channel: number): Float32Array;
    copyToChannel?(source: Float32Array, channelNumber: number, startInChannel?: number): void;
  }

  interface WebAudioContextNode {
    connect(destination: WebAudioContextNode): WebAudioContextNode;
    disconnect?(): void;
  }

  interface AudioBufferSourceNode extends WebAudioContextNode {
    buffer: AudioBuffer | null;
    onended?: (() => void) | null;
    start(when?: number, offset?: number, duration?: number): void;
    stop?(when?: number): void;
  }

  interface WebAudioContext {
    readonly state: "suspended" | "running" | "closed";
    readonly currentTime: number;
    readonly destination: WebAudioContextNode;
    readonly sampleRate: number;
    onstatechange?: (() => void) | null;
    createBuffer(numberOfChannels: number, length: number, sampleRate: number): AudioBuffer;
    createBufferSource(): AudioBufferSourceNode;
    resume(): Promise<void>;
    suspend(): Promise<void>;
    close(): Promise<void>;
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
    env: {
      USER_DATA_PATH: string;
    };
    login(option?: LoginOption): void;
    request<T = unknown>(option: RequestOption<T>): void;
    downloadFile(option: DownloadFileOption): void;
    connectSocket(option: ConnectSocketOption): SocketTask;
    getRecorderManager(): RecorderManager;
    getFileSystemManager(): FileSystemManager;
    createInnerAudioContext(): InnerAudioContext;
    createWebAudioContext?(): WebAudioContext;
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
    base64ToArrayBuffer(value: string): ArrayBuffer;
    getStorageSync<T = unknown>(key: string): T;
    setStorageSync<T = unknown>(key: string, value: T): void;
    removeStorageSync(key: string): void;
  }
}
