import type { Directive } from "vue";

const active = new WeakMap<HTMLElement, () => void>();
const dialogs: HTMLElement[] = [];
let originalOverflow = '';
export const modal: Directive<HTMLElement, () => void> = {
  mounted(element, binding) {
    const previous = document.activeElement as HTMLElement | null;
    if (!dialogs.length) originalOverflow = document.body.style.overflow;
    dialogs.push(element);
    document.body.style.overflow = "hidden";
    element.tabIndex = -1;
    const controls = () =>
      Array.from(
        element.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]',
        ),
      ).filter((e) => e.getClientRects().length);
    const keyboard = (event: KeyboardEvent) => {
      if (dialogs.at(-1) !== element) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        if (typeof binding.value === 'function') binding.value();
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
        (document.activeElement === first || document.activeElement === element || !element.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !element.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    // A disabled submitting button can leave focus on body; keep Escape and Tab working.
    document.addEventListener("keydown", keyboard);
    (controls()[0] || element).focus();
    active.set(element, () => {
      document.removeEventListener("keydown", keyboard);
      const wasTop = dialogs.at(-1) === element;
      dialogs.splice(dialogs.indexOf(element), 1);
      document.body.style.overflow = dialogs.length ? 'hidden' : originalOverflow;
      if (wasTop && previous?.isConnected) previous.focus();
    });
  },
  unmounted(element) {
    active.get(element)?.();
    active.delete(element);
  },
};
