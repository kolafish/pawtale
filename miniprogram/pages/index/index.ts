import { switchToHome } from "../../services/navigation";
import { definePage } from "../../services/page-definition";

definePage<Record<string, never>>()({
  data: {},

  onLoad() {
    switchToHome();
  },
});
