import { datasetNumber, type PageDatasetEvent } from "./page-input";

export interface VideoPlaybackData {
  videoPlaybackRate: number;
  videoPlaybackRates: number[];
  videoSpeedMenuOpen: boolean;
}

export function initialVideoPlaybackData(): VideoPlaybackData {
  return {
    videoPlaybackRate: 1,
    videoPlaybackRates: [1, 1.25, 1.5, 2],
    videoSpeedMenuOpen: false,
  };
}

interface VideoPlaybackHost {
  data: VideoPlaybackData;
  setData(patch: Partial<VideoPlaybackData>): void;
}

export const videoPlaybackMethods = {
  toggleVideoSpeedMenu(this: VideoPlaybackHost) {
    this.setData({ videoSpeedMenuOpen: !this.data.videoSpeedMenuOpen });
  },

  closeVideoSpeedMenu(this: VideoPlaybackHost) {
    this.setData({ videoSpeedMenuOpen: false });
  },

  changeVideoPlaybackRate(this: VideoPlaybackHost, event: PageDatasetEvent<{ rate?: number | string }>) {
    const rate = datasetNumber(event, "rate");
    if (!this.data.videoPlaybackRates.includes(rate)) return;
    wx.createVideoContext("memoir-video", this).playbackRate(rate);
    this.setData({ videoPlaybackRate: rate, videoSpeedMenuOpen: false });
  },

  applyVideoPlaybackRate(this: VideoPlaybackHost) {
    // A replay or a new source can recreate the native player and reset its speed.
    wx.createVideoContext("memoir-video", this).playbackRate(this.data.videoPlaybackRate);
    this.setData({ videoSpeedMenuOpen: false });
  },
};
