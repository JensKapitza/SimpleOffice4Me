(() => {
  "use strict";

  const button = document.createElement("button");
  const isEnglish = document.documentElement.lang.toLowerCase().startsWith("en");
  button.type = "button";
  button.className = "btn btn-primary rounded-circle shadow p-0";
  button.style.cssText = "position:fixed;right:1rem;bottom:1rem;z-index:1030;width:3rem;height:3rem;display:none;align-items:center;justify-content:center";
  button.setAttribute("aria-label", isEnglish ? "Scroll to top" : "Nach oben scrollen");
  button.title = isEnglish ? "Scroll to top" : "Nach oben scrollen";
  button.innerHTML = '<i class="fa-solid fa-arrow-up" aria-hidden="true"></i>';

  let framePending = false;
  const updateVisibility = () => {
    button.style.display = window.scrollY > 500 ? "inline-flex" : "none";
    framePending = false;
  };

  window.addEventListener("scroll", () => {
    if (framePending) return;
    framePending = true;
    window.requestAnimationFrame(updateVisibility);
  }, {passive: true});

  button.addEventListener("click", () => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({top: 0, behavior: reduceMotion ? "auto" : "smooth"});
  });

  document.body.append(button);
  updateVisibility();
})();
