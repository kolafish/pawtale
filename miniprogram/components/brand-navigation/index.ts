import { readLargeTextEnabled } from "../../services/accessibility-settings";
import { relaunchHome } from "../../services/navigation";

Component({
  properties: {
    title: { type: String, value: "" },
    home: { type: Boolean, value: false },
    pageClass: { type: String, value: "" },
  },
  data: {
    statusBarHeight: 20,
    brandRowHeight: 44,
    capsuleSpace: 104,
    navigationHeight: 112,
    largeText: false,
    canGoBack: false,
  },
  lifetimes: {
    attached() { this.refreshLayout(); },
    ready() { this.measureNavigation(); },
  },
  pageLifetimes: {
    show() { this.refreshLayout(); },
    resize() { this.refreshLayout(); },
  },
  observers: {
    "title, pageClass"() { this.refreshLayout(); },
  },
  methods: {
    refreshLayout() {
      const info = wx.getWindowInfo();
      const capsule = wx.getMenuButtonBoundingClientRect();
      const statusBarHeight = info.statusBarHeight;
      // Some desktop clients report an empty capsule rectangle.
      const validCapsule = capsule.height > 0 && capsule.top >= statusBarHeight && capsule.left > 0;
      const brandRowHeight = validCapsule
        ? Math.max(44, capsule.height + 2 * (capsule.top - statusBarHeight))
        : 44;
      this.setData({
        statusBarHeight,
        brandRowHeight,
        capsuleSpace: validCapsule ? info.windowWidth - capsule.left + 12 : 104,
        largeText: readLargeTextEnabled(),
        canGoBack: getCurrentPages().length > 1,
        navigationHeight: statusBarHeight + brandRowHeight + (this.properties.home ? 0 : 48),
      }, () => this.measureNavigation());
    },
    measureNavigation() {
      this.createSelectorQuery().select(".brand-navigation-bar").boundingClientRect(rect => {
        if (rect && rect.height > 0) {
          if (rect.height !== this.data.navigationHeight) {
            this.setData({ navigationHeight: rect.height });
          }
          this.triggerEvent("layout", { height: rect.height });
        }
      }).exec();
    },
    goBack() {
      if (getCurrentPages().length > 1) {
        wx.navigateBack({ delta: 1, fail: relaunchHome });
      } else {
        relaunchHome();
      }
    },
  },
});
