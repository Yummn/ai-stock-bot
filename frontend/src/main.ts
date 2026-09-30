import { createApp } from "vue";
import { modal } from "./modal";

async function mount() {
  const modern = import.meta.env.VITE_INVESTOR === "1";
  const lab = new URLSearchParams(location.search).has("lab");
  const App = modern
    ? (lab ? (await import("./ResearchApp.vue")).default : (await import("./InvestorApp.vue")).default)
    : (await import("./App.vue")).default;
  if (!modern) await import("./style.css");
  createApp(App).directive("modal", modal).mount("#app");
}
mount();
