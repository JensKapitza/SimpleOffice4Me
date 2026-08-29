(() => {
  "use strict";
  const root = document.documentElement;
  const preference = root.dataset.themePreference || "system";
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    const theme = preference === "system" ? (media.matches ? "dark" : "light") : preference;
    root.dataset.bsTheme = theme;
    root.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0b1220" : "#0f766e");
  };
  apply();
  if (preference === "system") media.addEventListener?.("change", apply);
})();
