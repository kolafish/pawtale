export function configureAudiblePlayback(audio: WechatMiniprogram.InnerAudioContext): void {
  wx.setInnerAudioOption?.({ obeyMuteSwitch: false, mixWithOther: true });
  audio.obeyMuteSwitch = false;
  audio.volume = 1;
}
