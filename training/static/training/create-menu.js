"use strict";
document.querySelectorAll(".create-menu").forEach(menu => {
  const trigger = menu.querySelector(".create-menu-trigger");
  const fallback = menu.querySelector(".create-menu-fallback");
  const options = menu.querySelector(".create-menu-options");
  if (!trigger || !fallback || !options) return;

  function setOpen(open, restoreFocus = false) {
    options.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
    if (restoreFocus) trigger.focus();
  }

  trigger.addEventListener("click", () => setOpen(options.hidden));
  menu.addEventListener("keydown", event => {
    if (event.key === "Escape" && !options.hidden) {
      setOpen(false, true);
      event.preventDefault();
    }
  });
  document.addEventListener("click", event => {
    if (!menu.contains(event.target)) setOpen(false);
  });
  menu.addEventListener("focusout", event => {
    if (event.relatedTarget && !menu.contains(event.relatedTarget)) setOpen(false);
  });

  // Keep an ordinary navigation link until the toggle is fully initialized.
  // Clicking Create content still reaches the same choices if this script fails.
  setOpen(false);
  trigger.hidden = false;
  fallback.hidden = true;
});
