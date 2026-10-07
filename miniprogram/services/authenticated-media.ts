import { ensureMemoirBackendSession } from "./memoir-api-client";
import { ApiError, readSessionCookie } from "./request";

export type MediaTransferTimeoutProfile = "media" | "export";
export type FileDownloadPhase = "checking" | "generating" | "downloading" | "opening";

const cachedDownloads = new Map<string, Promise<string>>();
const FILE_CACHE_FOLDER = "pawtale-exports-v1";
const MAX_CACHED_FILES = 20;
const MAX_CACHED_BYTES = 40 * 1024 * 1024;

interface FileDownloadCache {
  key: string;
  filename: string;
  ready: boolean;
  sessionCookie: string;
  onPhase?: (phase: FileDownloadPhase) => void;
}

const MEDIA_TRANSFER_TIMEOUT_MS: Record<MediaTransferTimeoutProfile, number> = {
  media: 60000,
  export: 300000,
};

interface AuthenticatedDownloadOptions {
  failureMessage: string;
  domainLabel: string;
  timeoutProfile?: MediaTransferTimeoutProfile;
  timeoutMs?: number;
  cache?: FileDownloadCache;
}

function cacheFilePath(cache: FileDownloadCache): string {
  if (!/^[a-f0-9]{64}$/.test(cache.key)) throw new Error("导出文件标识无效");
  const filename = cache.filename.replace(/[\\/:*?"<>|\r\n]/g, "-").trim().slice(0, 100);
  if (!filename || filename === "." || filename === "..") throw new Error("导出文件名无效");
  return `${wx.env.USER_DATA_PATH}/${FILE_CACHE_FOLDER}/${cache.key}/${filename}`;
}

function assertDownloadSession(cache: FileDownloadCache): void {
  if (readSessionCookie() !== cache.sessionCookie) {
    throw new ApiError(409, "登录状态已变化，请重新打开书稿", {});
  }
}

function pruneCachedFiles(root: string, keepKey: string, incomingBytes = 0): void {
  const fs = wx.getFileSystemManager();
  const entries = fs.readdirSync(root).filter((key) => /^[a-f0-9]{64}$/.test(key)).flatMap((key) => {
    if (key !== keepKey && [...cachedDownloads.keys()].some((pending) => pending.endsWith(`:${key}`))) return [];
    const files = fs.readdirSync(`${root}/${key}`).filter((name) => /\.(pdf|txt)$/.test(name));
    if (!files.length) return [];
    const stats = files.map((name) => fs.statSync(`${root}/${key}/${name}`));
    return [{ key, size: stats.reduce((sum, stat) => sum + stat.size, 0), modified: Math.max(...stats.map((stat) => stat.lastModifiedTime)) }];
  }).sort((a, b) => a.modified - b.modified);
  let bytes = incomingBytes + entries.reduce((sum, entry) => sum + entry.size, 0);
  let count = entries.length + (incomingBytes > 0 ? 1 : 0);
  for (const entry of entries) {
    if (count <= MAX_CACHED_FILES && bytes <= MAX_CACHED_BYTES) break;
    if (entry.key === keepKey) continue;
    fs.rmdirSync(`${root}/${entry.key}`, true);
    count -= 1;
    bytes -= entry.size;
  }
}

interface AuthenticatedUploadOptions<T> {
  filePath: string;
  name: string;
  failureMessage: string;
  domainLabel: string;
  parseResponse: (data: string) => T;
  responseErrorMessage?: (data: string) => string;
  formData?: Record<string, string>;
  timeoutProfile?: MediaTransferTimeoutProfile;
  timeoutMs?: number;
}

export function mediaTransferTimeoutMs(
  profile: MediaTransferTimeoutProfile = "media",
): number {
  return MEDIA_TRANSFER_TIMEOUT_MS[profile];
}

function transferDomain(url: string): string {
  const parts = url.split("/");
  return parts.length >= 3 && parts[0].startsWith("http")
    ? parts.slice(0, 3).join("/")
    : url;
}

function transferErrorMessage(
  errMsg: string | undefined,
  url: string,
  domainLabel: string,
  apiName: "downloadFile" | "uploadFile",
  fallback: string,
): string {
  const message = String(errMsg || "");
  if (message.toLowerCase().includes("url not in domain list")) {
    return `${domainLabel}域名未配置，请在微信公众平台把 ${transferDomain(url)} 添加到 ${apiName} 合法域名。`;
  }
  if (message.toLowerCase().includes("timeout")) {
    return `${domainLabel}超时，请检查网络后重试。`;
  }
  return message || fallback;
}

function sessionHeader(): Record<string, string> {
  const cookie = readSessionCookie();
  return cookie ? { Cookie: cookie } : {};
}

export async function downloadAuthenticatedFile(
  url: string,
  options: AuthenticatedDownloadOptions,
): Promise<string> {
  await ensureMemoirBackendSession();
  const cache = options.cache;
  if (cache) {
    assertDownloadSession(cache);
    const path = cacheFilePath(cache);
    try {
      if (wx.getFileSystemManager().statSync(path).size > 0) {
        cache.onPhase?.("opening");
        return path;
      }
    } catch { /* A discarded local file is downloaded again. */ }
    const pendingKey = `${cache.sessionCookie}:${cache.key}`;
    const pending = cachedDownloads.get(pendingKey);
    cache.onPhase?.(cache.ready ? "downloading" : "generating");
    if (pending) return pending;
    const transfer = downloadFile(url, options).then((temporaryPath) => {
      assertDownloadSession(cache);
      const fs = wx.getFileSystemManager();
      const directory = path.slice(0, path.lastIndexOf("/"));
      const root = `${wx.env.USER_DATA_PATH}/${FILE_CACHE_FOLDER}`;
      try {
        fs.mkdirSync(directory, true);
        pruneCachedFiles(root, cache.key, fs.statSync(temporaryPath).size);
        fs.copyFileSync(temporaryPath, `${path}.part`);
        fs.renameSync(`${path}.part`, path);
      } catch {
        try { fs.unlinkSync(`${path}.part`); } catch { /* The copy may not have created a file. */ }
        throw new Error("保存导出文件失败，请清理微信存储空间后重试");
      }
      pruneCachedFiles(root, cache.key);
      cache.onPhase?.("opening");
      return path;
    }).finally(() => cachedDownloads.delete(pendingKey));
    cachedDownloads.set(pendingKey, transfer);
    return transfer;
  }
  return downloadFile(url, options);
}

// For links anyone may open, such as a forwarded share video; no login is attempted.
export function downloadPublicFile(url: string, options: AuthenticatedDownloadOptions): Promise<string> {
  return downloadFile(url, options);
}

function downloadFile(url: string, options: AuthenticatedDownloadOptions): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url,
      timeout: options.timeoutMs ?? mediaTransferTimeoutMs(options.timeoutProfile),
      header: sessionHeader(),
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.tempFilePath);
          return;
        }
        reject(new ApiError(result.statusCode, options.failureMessage, result));
      },
      fail: (error) => {
        reject(new ApiError(
          0,
          transferErrorMessage(
            error.errMsg,
            url,
            options.domainLabel,
            "downloadFile",
            options.failureMessage,
          ),
          error,
        ));
      },
    });
  });
}

export async function uploadAuthenticatedFile<T>(
  url: string,
  options: AuthenticatedUploadOptions<T>,
): Promise<T> {
  await ensureMemoirBackendSession();
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url,
      filePath: options.filePath,
      name: options.name,
      timeout: options.timeoutMs ?? mediaTransferTimeoutMs(options.timeoutProfile),
      ...(options.formData ? { formData: options.formData } : {}),
      header: sessionHeader(),
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) {
          try {
            resolve(options.parseResponse(result.data));
          } catch (error) {
            reject(error);
          }
          return;
        }
        reject(new ApiError(
          result.statusCode,
          options.responseErrorMessage?.(result.data) || options.failureMessage,
          result.data,
        ));
      },
      fail: (error) => {
        reject(new ApiError(
          0,
          transferErrorMessage(
            error.errMsg,
            url,
            options.domainLabel,
            "uploadFile",
            options.failureMessage,
          ),
          error,
        ));
      },
    });
  });
}

function isChooseImageCancel(error: WechatMiniprogram.GeneralCallbackResult): boolean {
  return String(error.errMsg || "").toLowerCase().includes("cancel");
}

export function chooseImageFile(): Promise<string | null> {
  if (typeof wx.chooseMedia === "function") {
    return new Promise((resolve, reject) => {
      wx.chooseMedia?.({
        count: 1,
        mediaType: ["image"],
        sourceType: ["album", "camera"],
        sizeType: ["original", "compressed"],
        success: (result) => resolve(result.tempFiles[0]?.tempFilePath || null),
        fail: (error) => {
          if (isChooseImageCancel(error)) {
            resolve(null);
            return;
          }
          reject(new Error(error.errMsg || "选择图片失败"));
        },
      });
    });
  }
  return new Promise((resolve, reject) => {
    wx.chooseImage({
      count: 1,
      sourceType: ["album", "camera"],
      sizeType: ["original", "compressed"],
      success: (result) => resolve(result.tempFilePaths[0] || null),
      fail: (error) => {
        if (isChooseImageCancel(error)) {
          resolve(null);
          return;
        }
        reject(new Error(error.errMsg || "选择图片失败"));
      },
    });
  });
}

export function editImageFile(src: string): Promise<string | null> {
  if (typeof wx.editImage !== "function") {
    return Promise.reject(new Error("当前微信版本不支持图片编辑，请升级微信后再试"));
  }
  return new Promise((resolve, reject) => {
    wx.editImage!({
      src,
      success: (result) => result.tempFilePath ? resolve(result.tempFilePath) : reject(new Error("图片编辑未返回结果，请重试")),
      fail: (error) => isChooseImageCancel(error) ? resolve(null) : reject(new Error(error.errMsg || "图片编辑失败")),
    });
  });
}

// Lets the user frame a photo at a fixed ratio. Resolves null on cancel, or
// when this WeChat version has no crop page, so callers keep the original.
export function cropImageFile(src: string, cropScale: WechatMiniprogram.CropImageOption["cropScale"]): Promise<string | null> {
  if (typeof wx.cropImage !== "function") return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    wx.cropImage!({
      src,
      cropScale,
      success: (result) => resolve(result.tempFilePath || null),
      fail: (error) => isChooseImageCancel(error) ? resolve(null) : reject(new Error(error.errMsg || "裁剪图片失败")),
    });
  });
}
