Component({
  properties: {
    value: { type: String, value: "" },
    disabled: { type: Boolean, value: false },
    pageClass: { type: String, value: "" },
  },
  methods: {
    onInput(event: { detail: { value: string } }) {
      if (!this.properties.disabled) this.triggerEvent("change", { value: event.detail.value });
    },
  },
});
