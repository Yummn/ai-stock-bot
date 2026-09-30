import type { Directive } from "vue";

const active = new WeakMap<HTMLElement, () => void>();
export const modal: Directive<HTMLElement, () => void> = {
  mounted(element, binding) {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.tabIndex = -1;
    const controls = () =>
      Array.from(
        element.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]',
        ),
      ).filter((e) => e.getClientRects().length);
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        binding.value();
      }
      if (event.key !== "Tab") return;
      const list = controls();
      const first = list[0],
        last = list[list.length - 1];
      if (!first) {
        event.preventDefault();
        element.focus();
      } else if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === element)
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    element.addEventListener("keydown", keyboard);
    (controls()[0] || element).focus();
    active.set(element, () => {
      element.removeEventListener("keydown", keyboard);
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    });
  },
  unmounted(element) {
    active.get(element)?.();
    active.delete(element);
  },
};
