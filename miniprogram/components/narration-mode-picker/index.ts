import { isNarrationMode, NARRATION_MODE_OPTIONS } from "../../domain/memoir-narration-mode";
import { datasetString, type PageDatasetEvent } from "../../services/page-input";

Component({
  properties: {
    selected: { type: String, value: "free_narration" },
    disabled: { type: Boolean, value: false },
    locked: { type: Boolean, value: false },
    compact: { type: Boolean, value: false },
    largeText: { type: Boolean, value: false },
  },
  data: { options: NARRATION_MODE_OPTIONS },
  methods: {
    choose(event: PageDatasetEvent<{ mode?: string }>) {
      const value = datasetString(event, "mode");
      if (this.properties.disabled || this.properties.locked || !isNarrationMode(value) || value === this.properties.selected) return;
      this.triggerEvent("change", { value });
    },
  },
});
