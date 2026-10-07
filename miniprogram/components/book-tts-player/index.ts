Component({
  properties: {
    preparingText: { type: String, value: "" },
    title: {
      type: String,
      value: "正文朗读",
    },
    phase: {
      type: String,
      value: "preparing",
    },
    progress: {
      type: Number,
      value: 0,
    },
    spokenText: {
      type: String,
      value: "",
    },
    currentText: {
      type: String,
      value: "",
    },
    remainingText: {
      type: String,
      value: "",
    },
  },

  methods: {
    stopPlayback() {
      this.triggerEvent("stop");
    },
  },
});
