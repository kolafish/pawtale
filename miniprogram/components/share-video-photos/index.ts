import type { MemoirImageAsset } from "../../domain/memoir-book";
import { chooseImageFiles } from "../../services/authenticated-media";
import {
  downloadMemoirImageAssetFile,
  listMemoirImageAssets,
  uploadMemoirImageAsset,
} from "../../services/memoir-book-api";

interface PhotoView {
  id: string;
  path: string;
  caption?: string;
  uploaded?: boolean;
}

interface PickerItem extends PhotoView {
  // 1-based position in the new selection, 0 when not chosen.
  order: number;
}

interface GridRect {
  left: number;
  top: number;
  width: number;
}

// Matches the four square cells per row in index.wxss.
const GRID_COLUMNS = 4;

type IndexEvent = { currentTarget: { dataset: { index?: number | string } } };
type TouchEvent = IndexEvent & { touches: Array<{ clientX: number; clientY: number }> };

export function movePhoto<T>(items: T[], from: number, to: number): T[] {
  const next = items.slice();
  next.splice(to, 0, ...next.splice(from, 1));
  return next;
}

// The slot under the finger, clamped to the photos so the add button never moves.
export function slotAt(grid: GridRect, x: number, y: number, count: number): number {
  const cell = grid.width / GRID_COLUMNS;
  const column = Math.min(GRID_COLUMNS - 1, Math.max(0, Math.floor((x - grid.left) / cell)));
  const row = Math.max(0, Math.floor((y - grid.top) / cell));
  return Math.min(count - 1, row * GRID_COLUMNS + column);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

Component({
  properties: {
    projectId: { type: String, value: "" },
    photoIds: { type: Array, value: [] },
    maxCount: { type: Number, value: 20 },
    disabled: { type: Boolean, value: false },
    editCaptions: { type: Boolean, value: false },
  },

  data: {
    photos: [] as PhotoView[],
    paths: {} as Record<string, string>,
    assets: {} as Record<string, { caption: string; uploaded: boolean }>,
    busy: false,
    statusText: "",
    dragIndex: -1,
    grid: null as GridRect | null,
    pickerOpen: false,
    pickerLoading: false,
    pickerItems: [] as PickerItem[],
    pickerCount: 0,
  },

  observers: {
    photoIds(value: unknown) {
      const ids = value as string[];
      this.setData({ photos: ids.map((id) => ({ id, path: this.data.paths[id] || "", ...this.data.assets[id] })) });
      void this.loadPaths(ids);
    },
  },

  methods: {
    ids(): string[] {
      return this.properties.photoIds as string[];
    },

    emitChange(photoIds: string[]) {
      this.triggerEvent("change", { photoIds });
    },

    setBusy(busy: boolean, statusText = "") {
      this.setData({ busy, statusText });
      this.triggerEvent("busy", { busy });
    },

    rememberPath(id: string, path: string) {
      const paths = { ...this.data.paths, [id]: path };
      this.setData({
        paths,
        photos: this.data.photos.map((photo) => ({ ...photo, path: paths[photo.id] || "" })),
        pickerItems: this.data.pickerItems.map((item) => ({ ...item, path: paths[item.id] || "" })),
      });
    },

    rememberAssets(items: MemoirImageAsset[]) {
      const assets = { ...this.data.assets };
      for (const asset of items) assets[asset.id] = { caption: asset.caption || "", uploaded: asset.source_type === "manual_upload" };
      this.setData({ assets, photos: this.data.photos.map(photo => ({ ...photo, ...assets[photo.id] })) });
    },

    onPhotoCaptionBusy(event: { detail: { busy: boolean } }) {
      this.setBusy(event.detail.busy, event.detail.busy ? "正在保存照片说明" : "");
    },

    onPhotoCaptionSaved(event: { detail: { asset: MemoirImageAsset } }) {
      this.rememberAssets([event.detail.asset]);
      this.emitChange(this.ids());
    },

    async loadPaths(ids: string[]) {
      if (this.properties.editCaptions && ids.some(id => !this.data.assets[id])) {
        try {
          const { items } = await listMemoirImageAssets(this.properties.projectId);
          this.rememberAssets(items);
        } catch (error) {
          this.triggerEvent("error", { message: errorMessage(error, "照片说明读取失败，请重新打开") });
        }
      }
      for (const id of ids) {
        if (this.data.paths[id]) continue;
        try {
          this.rememberPath(id, await downloadMemoirImageAssetFile(this.properties.projectId, id, true));
        } catch (error) {
          console.warn("share video photo thumbnail unavailable", error);
        }
      }
    },

    addPhotos() {
      if (this.properties.disabled || this.data.busy) return;
      const remaining = this.properties.maxCount - this.ids().length;
      if (remaining <= 0) {
        wx.showToast({ title: `这段最多放 ${this.properties.maxCount} 张`, icon: "none" });
        return;
      }
      wx.showActionSheet({
        itemList: ["从手机相册选", "从图片素材选"],
        success: ({ tapIndex }) => {
          if (tapIndex === 0) void this.uploadFromAlbum(remaining);
          else void this.openPicker();
        },
      });
    },

    async uploadFromAlbum(remaining: number) {
      let files: string[];
      try {
        files = await chooseImageFiles(remaining);
      } catch (error) {
        this.triggerEvent("error", { message: errorMessage(error, "选择照片失败") });
        return;
      }
      if (!files.length) return;
      const added: string[] = [];
      let failure = "";
      for (const [index, file] of files.entries()) {
        this.setBusy(true, `正在上传第 ${index + 1}/${files.length} 张`);
        try {
          const asset = await uploadMemoirImageAsset(this.properties.projectId, file);
          // The picked file already is a local preview of what was uploaded.
          this.rememberAssets([asset]);
          this.rememberPath(asset.id, file);
          added.push(asset.id);
        } catch (error) {
          failure = errorMessage(error, "照片上传失败");
        }
      }
      this.setBusy(false);
      const current = this.ids();
      this.emitChange([...current, ...added.filter((id, index) => !current.includes(id) && added.indexOf(id) === index)]);
      if (failure) {
        this.triggerEvent("error", { message: `有 ${files.length - added.length} 张照片没传上去：${failure}` });
      }
    },

    async openPicker() {
      this.setData({ pickerOpen: true, pickerLoading: true, pickerItems: [], pickerCount: 0 });
      try {
        const { items } = await listMemoirImageAssets(this.properties.projectId);
        this.rememberAssets(items);
        const chosen = this.ids();
        const pickerItems = items
          .filter((asset) => asset.status === "ready")
          .map((asset) => ({ id: asset.id, path: this.data.paths[asset.id] || "", order: chosen.indexOf(asset.id) + 1 }));
        // Chosen photos that are no longer in the library drop out of the numbering.
        const kept = pickerItems.filter((item) => item.order).sort((a, b) => a.order - b.order);
        kept.forEach((item, index) => { item.order = index + 1; });
        this.setData({ pickerItems, pickerCount: kept.length, pickerLoading: false });
        void this.loadPaths(pickerItems.map((item) => item.id));
      } catch (error) {
        this.setData({ pickerOpen: false, pickerLoading: false });
        this.triggerEvent("error", { message: errorMessage(error, "图片素材读取失败") });
      }
    },

    togglePickerItem(event: IndexEvent) {
      const index = Number(event.currentTarget.dataset.index);
      const target = this.data.pickerItems[index];
      if (!target) return;
      if (!target.order && this.data.pickerCount >= this.properties.maxCount) {
        wx.showToast({ title: `这段最多放 ${this.properties.maxCount} 张`, icon: "none" });
        return;
      }
      const pickerItems = this.data.pickerItems.map((item) => {
        if (item.id === target.id) return { ...item, order: target.order ? 0 : this.data.pickerCount + 1 };
        return target.order && item.order > target.order ? { ...item, order: item.order - 1 } : item;
      });
      this.setData({ pickerItems, pickerCount: this.data.pickerCount + (target.order ? -1 : 1) });
    },

    confirmPicker() {
      const chosen = this.data.pickerItems.filter((item) => item.order).sort((a, b) => a.order - b.order);
      this.setData({ pickerOpen: false, pickerItems: [] });
      this.emitChange(chosen.map((item) => item.id));
    },

    // Stops the page behind the sheet from scrolling.
    keepPageStill() {},

    closePicker() {
      this.setData({ pickerOpen: false, pickerItems: [] });
    },

    removePhoto(event: IndexEvent) {
      if (this.properties.disabled || this.data.busy) return;
      const index = Number(event.currentTarget.dataset.index);
      this.emitChange(this.ids().filter((_id, position) => position !== index));
    },

    startDrag(event: IndexEvent) {
      if (this.properties.disabled || this.data.busy || this.data.photos.length < 2) return;
      const index = Number(event.currentTarget.dataset.index);
      this.createSelectorQuery()
        .select(".photo-grid")
        .boundingClientRect((grid) => {
          if (grid) this.setData({ grid, dragIndex: index });
        })
        .exec();
    },

    moveDrag(event: TouchEvent) {
      const { dragIndex, grid, photos } = this.data;
      const touch = event.touches[0];
      if (dragIndex < 0 || !grid || !touch) return;
      const target = slotAt(grid, touch.clientX, touch.clientY, photos.length);
      if (target !== dragIndex) {
        this.setData({ photos: movePhoto(photos, dragIndex, target), dragIndex: target });
      }
    },

    endDrag() {
      if (this.data.dragIndex < 0) return;
      this.setData({ dragIndex: -1 });
      const ids = this.data.photos.map((photo) => photo.id);
      if (ids.join() !== this.ids().join()) this.emitChange(ids);
    },
  },
});
