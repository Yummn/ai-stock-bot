import { createApp } from "vue";
import { modal } from "./modal";

async function mount() {
  const investor =
    import.meta.env.VITE_INVESTOR === "1" &&
    !new URLSearchParams(location.search).has("lab");
  const App = investor
    ? (await import("./InvestorApp.vue")).default
    : (await import("./App.vue")).default;
  if (!investor) await import("./style.css");
  createApp(App).directive("modal", modal).mount("#app");
}
mount();
