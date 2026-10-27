Component({
  properties: {
    options: { type: Array, value: [] },
    value: { type: String, value: "" },
    disabled: { type: Boolean, value: false },
  },
  methods: {
    selectStyle(event: { currentTarget: { dataset: { style: string } } }) {
      if (!this.properties.disabled) this.triggerEvent("change", { value: event.currentTarget.dataset.style });
    },
  },
});
