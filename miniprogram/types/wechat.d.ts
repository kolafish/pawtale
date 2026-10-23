declare const wx: WechatMiniprogram.Wx;

interface MiniProgramPageStackItem {
  route?: string;
}

declare function getCurrentPages(): MiniProgramPageStackItem[];

declare function App<T extends Record<string, unknown>>(options: T): void;

declare function Page<TData extends object, TMethods extends object>(
  options: TMethods & {
    data: TData;
    onLoad?(query?: Record<string, string | undefined>): void | Promise<void>;
    onShow?(): void | Promise<void>;
    onShareAppMessage?(option?: WechatMiniprogram.ShareAppMessageOption): WechatMiniprogram.ShareAppMessagePayload;
    onShareTimeline?(): WechatMiniprogram.ShareTimelinePayload;
  } & ThisType<TMethods & {
    data: TData;
    setData(data: Partial<TData>, callback?: () => void): void;
  }>
): void;

type ComponentPropertyValue<T> =
  T extends { type: StringConstructor } ? string
  : T extends { type: BooleanConstructor } ? boolean
  : T extends { type: NumberConstructor } ? number
  : T extends { type: ArrayConstructor } ? unknown[]
  : unknown;

type ComponentInstance<
  TProperties extends object,
  TData extends object,
  TMethods extends object,
> = TMethods & {
  data: TData;
  properties: {
    [TKey in keyof TProperties]: ComponentPropertyValue<TProperties[TKey]>;
  };
  setData(data: Partial<TData>, callback?: () => void): void;
  triggerEvent(name: string, detail?: unknown): void;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
};

declare function Component<
  TProperties extends object,
  TData extends object = object,
  TMethods extends object = object,
>(
  options: {
    options?: {
      multipleSlots?: boolean;
    };
    properties: TProperties;
    data?: TData;
    methods: TMethods & ThisType<ComponentInstance<TProperties, TData, TMethods>>;
    observers?: Record<string, (...values: unknown[]) => void>
      & ThisType<ComponentInstance<TProperties, TData, TMethods>>;
    lifetimes?: {
      attached?(): void;
      detached?(): void;
    } & ThisType<ComponentInstance<TProperties, TData, TMethods>>;
    pageLifetimes?: {
      show?(): void;
      hide?(): void;
      resize?(): void;
    } & ThisType<ComponentInstance<TProperties, TData, TMethods>>;
  },
): void;

declare namespace WechatMiniprogram {
  interface BoundingClientRectResult { top: number; bottom: number; left: number; right: number; width: number; height: number }
  interface NodesRef {
    boundingClientRect(callback: (rect: BoundingClientRectResult | null) => void): SelectorQuery;
    scrollOffset(callback: (offset: { scrollTop: number; scrollLeft: number }) => void): SelectorQuery;
  }
  interface SelectorQuery { select(selector: string): NodesRef; selectViewport(): NodesRef; exec(): void }

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

  interface UploadFileSuccessCallbackResult extends GeneralCallbackResult {
    data: string;
    statusCode: number;
  }

  interface UploadFileOption {
    url: string;
    filePath: string;
    name: string;
    header?: Record<string, string>;
    formData?: Record<string, string | number | boolean>;
    timeout?: number;
    success?(result: UploadFileSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface ChooseImageSuccessCallbackResult extends GeneralCallbackResult {
    tempFilePaths: string[];
  }

  interface ChooseImageOption {
    count?: number;
    sizeType?: Array<"original" | "compressed">;
    sourceType?: Array<"album" | "camera">;
    success?(result: ChooseImageSuccessCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface EditImageOption {
    src: string;
    success?(result: GeneralCallbackResult & { tempFilePath: string }): void;
    fail?(result: GeneralCallbackResult): void;
  }

  interface CropImageOption {
    src: string;
    cropScale: "16:9" | "9:16" | "4:3" | "3:4" | "5:4" | "4:5" | "1:1";
    success?(result: GeneralCallbackResult & { tempFilePath: string }): void;
    fail?(result: GeneralCallbackResult): void;
  }

  interface ChooseMediaFile {
    tempFilePath: string;
    size: number;
    duration?: number;
    height?: number;
    width?: number;
    thumbTempFilePath?: string;
    fileType?: "image" | "video";
  }

  interface ChooseMediaSuccessCallbackResult extends GeneralCallbackResult {
    tempFiles: ChooseMediaFile[];
    type: "image" | "video" | "mix";
  }

  interface ChooseMediaOption {
    count?: number;
    mediaType?: Array<"image" | "video" | "mix">;
    sourceType?: Array<"album" | "camera">;
    sizeType?: Array<"original" | "compressed">;
    maxDuration?: number;
    success?(result: ChooseMediaSuccessCallbackResult): void;
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
    audioMode?: "default" | "voiceChat" | "voiceRecognition" | "recording";
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
    accessSync(path: string): void;
    mkdirSync(dirPath: string, recursive?: boolean): void;
    copyFileSync(srcPath: string, destPath: string): void;
    renameSync(oldPath: string, newPath: string): void;
    readdirSync(dirPath: string): string[];
    statSync(path: string): { size: number; lastModifiedTime: number };
    unlinkSync(filePath: string): void;
    rmdirSync(dirPath: string, recursive?: boolean): void;
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

  interface VideoContext {
    playbackRate(rate: number): void;
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
    success?: (result: GeneralCallbackResult) => void;
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
    editable?: boolean;
    placeholderText?: string;
    confirmText?: string;
    confirmColor?: string;
    cancelText?: string;
    cancelColor?: string;
    showCancel?: boolean;
    success?(result: { confirm: boolean; cancel: boolean; content?: string; errMsg?: string }): void;
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

  interface PrivacyCallbackOption {
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface AuthorizationSetting {
    "scope.record"?: boolean;
    [scope: string]: boolean | undefined;
  }

  interface GetSettingOption {
    success?(result: { authSetting: AuthorizationSetting; errMsg?: string }): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface AuthorizeOption {
    scope: "scope.record";
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface OpenSettingOption {
    success?(result: { authSetting: AuthorizationSetting; errMsg?: string }): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface ShareAppMessageOption {
    from: "button" | "menu";
    target?: {
      dataset?: Record<string, string | undefined>;
    };
    webViewUrl?: string;
  }

  interface ShareAppMessagePayload {
    title: string;
    path?: string;
    imageUrl?: string;
  }

  interface ShareTimelinePayload {
    title: string;
    query?: string;
    imageUrl?: string;
  }

  interface ShowShareMenuOption {
    withShareTicket?: boolean;
    menus?: Array<"shareAppMessage" | "shareTimeline">;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface ShareFileMessageOption {
    filePath: string;
    fileName?: string;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface SaveVideoToPhotosAlbumOption {
    filePath: string;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
  }

  interface PreviewImageOption {
    current?: string;
    urls: string[];
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult): void;
    complete?(result: GeneralCallbackResult): void;
  }

  interface RequestVirtualPaymentOption {
    signData: string;
    paySig: string;
    signature: string;
    mode: string;
    success?(result: GeneralCallbackResult): void;
    fail?(result: GeneralCallbackResult & { errCode?: number }): void;
  }

  interface DeviceInfo {
    platform: string;
    system: string;
  }

  interface AppBaseInfo {
    version: string;
    SDKVersion: string;
  }

  interface MemoryWarningResult {
    level?: number;
  }

  interface Wx {
    env: {
      USER_DATA_PATH: string;
    };
    login(option?: LoginOption): void;
    requestVirtualPayment?(option: RequestVirtualPaymentOption): void;
    getDeviceInfo?(): DeviceInfo;
    getAppBaseInfo?(): AppBaseInfo;
    canIUse?(schema: string): boolean;
    request<T = unknown>(option: RequestOption<T>): void;
    downloadFile(option: DownloadFileOption): void;
    uploadFile(option: UploadFileOption): void;
    chooseImage(option: ChooseImageOption): void;
    chooseMedia?(option: ChooseMediaOption): void;
    editImage?(option: EditImageOption): void;
    cropImage?(option: CropImageOption): void;
    connectSocket(option: ConnectSocketOption): SocketTask;
    getRecorderManager(): RecorderManager;
    getFileSystemManager(): FileSystemManager;
    createInnerAudioContext(): InnerAudioContext;
    createVideoContext(id: string, component?: object): VideoContext;
    createWebAudioContext?(): WebAudioContext;
    getPrivacySetting?(option: PrivacySettingOption): void;
    requirePrivacyAuthorize?(option: PrivacyCallbackOption): void;
    openPrivacyContract?(option: PrivacyCallbackOption): void;
    getSetting?(option: GetSettingOption): void;
    authorize?(option: AuthorizeOption): void;
    openSetting?(option: OpenSettingOption): void;
    showShareMenu?(option: ShowShareMenuOption): void;
    hideShareMenu?(): void;
    shareFileMessage?(option: ShareFileMessageOption): void;
    saveVideoToPhotosAlbum?(option: SaveVideoToPhotosAlbumOption): void;
    previewImage(option: PreviewImageOption): void;
    enableAlertBeforeUnload?(option: { message: string }): void;
    disableAlertBeforeUnload?(option?: Record<string, never>): void;
    setInnerAudioOption?(option: InnerAudioOption): void;
    setKeepScreenOn(option: {
      keepScreenOn: boolean;
      fail?(result: GeneralCallbackResult): void;
    }): void;
    navigateTo(option: NavigateOption): void;
    redirectTo(option: NavigateOption): void;
    reLaunch(option: NavigateOption): void;
    switchTab(option: NavigateOption): void;
    navigateBack(option?: NavigateBackOption): void;
    createSelectorQuery(): SelectorQuery;
    pageScrollTo(option: { scrollTop?: number; selector?: string; duration?: number }): void;
    showActionSheet(option: ActionSheetOption): void;
    showModal(option: ModalOption): void;
    showToast(option: ToastOption): void;
    showLoading(option: LoadingOption): void;
    hideLoading(): void;
    stopPullDownRefresh(): void;
    base64ToArrayBuffer(value: string): ArrayBuffer;
    getStorageSync<T = unknown>(key: string): T;
    getStorageInfoSync(): { keys: string[] };
    setStorageSync<T = unknown>(key: string, value: T): void;
    removeStorageSync(key: string): void;
    onMemoryWarning?(listener: (result: MemoryWarningResult) => void): void;
  }
}
