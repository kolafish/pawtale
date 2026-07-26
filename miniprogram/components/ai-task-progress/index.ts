Component({
  properties: {
    title: {
      type: String,
      value: "",
    },
    detail: {
      type: String,
      value: "",
    },
    mode: {
      type: String,
      value: "hidden",
    },
    progress: {
      type: Number,
      value: 0,
    },
    canCancel: {
      type: Boolean,
      value: false,
    },
    cancelText: {
      type: String,
      value: "取消处理",
    },
  },

  methods: {
    cancelTask() {
      this.triggerEvent("cancel");
    },
  },
});
