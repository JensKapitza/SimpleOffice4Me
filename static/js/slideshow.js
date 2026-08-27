(function () {
  "use strict";

  function initializeSlideshow() {
    const modalElement = document.getElementById("slideshow");
    const carouselElement = document.getElementById("image-carousel");
    const seconds = document.getElementById("slide-seconds");
    const secondsValue = document.getElementById("slide-seconds-value");
    const autostart = document.getElementById("slide-autostart");
    const start = document.getElementById("slide-autoplay-start");
    const stop = document.getElementById("slide-autoplay-stop");
    const counter = document.getElementById("slide-counter");
    const originalLink = document.getElementById("slide-open-original");
    const infoToggle = document.getElementById("slide-info-toggle");
    if (!modalElement || !carouselElement || !seconds || !secondsValue || !autostart ||
        !start || !stop || !counter || !originalLink || !infoToggle || !window.bootstrap?.Carousel) return;

    const originals = Array.from(carouselElement.querySelectorAll(".carousel-item"))
      .map(item => item.dataset.originalUrl);
    const carousel = window.bootstrap.Carousel.getOrCreateInstance(carouselElement, {
      interval: false,
      ride: false,
      pause: false,
      wrap: true
    });
    let timer = null;
    let playing = false;
    const delay = () => Math.min(60, Math.max(3, Number(seconds.value) || 5)) * 1000;
    const updateDelayLabel = () => { secondsValue.textContent = String(Math.round(delay() / 1000)); };
    const information = Array.from(carouselElement.querySelectorAll("[data-slide-information]"));

    function setInformationVisible(visible) {
      information.forEach(panel => { panel.hidden = !visible; });
      infoToggle.setAttribute("aria-expanded", String(visible));
      infoToggle.querySelector("span").textContent = visible ? infoToggle.dataset.hideLabel : infoToggle.dataset.showLabel;
      try { window.localStorage.setItem("simpleoffice-slideshow-information", visible ? "show" : "hide"); } catch (_error) { /* storage is optional */ }
    }

    function stopAutoplay() {
      playing = false;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      start.disabled = false;
      stop.disabled = true;
    }

    function schedule() {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      if (playing && originals.length > 1) {
        timer = window.setTimeout(() => carousel.next(), delay());
      }
    }

    function startAutoplay() {
      if (originals.length < 2) return;
      playing = true;
      start.disabled = true;
      stop.disabled = false;
      schedule();
    }

    start.addEventListener("click", startAutoplay);
    stop.addEventListener("click", stopAutoplay);
    infoToggle.addEventListener("click", () => setInformationVisible(infoToggle.getAttribute("aria-expanded") !== "true"));
    seconds.addEventListener("input", () => { updateDelayLabel(); if (playing) schedule(); });
    autostart.addEventListener("change", () => {
      if (autostart.checked) startAutoplay();
      else stopAutoplay();
    });
    modalElement.addEventListener("shown.bs.modal", () => {
      if (autostart.checked) startAutoplay();
    });
    modalElement.addEventListener("hidden.bs.modal", stopAutoplay);
    carouselElement.querySelectorAll("[data-bs-slide], [data-bs-slide-to]").forEach(control => {
      control.addEventListener("click", () => { if (playing) schedule(); });
    });
    carouselElement.addEventListener("slid.bs.carousel", event => {
      const index = Number.isInteger(event.to) ? event.to : 0;
      counter.textContent = `${index + 1} / ${originals.length}`;
      originalLink.href = originals[index];
      schedule();
    });
    updateDelayLabel();
    let informationVisible = true;
    try { informationVisible = window.localStorage.getItem("simpleoffice-slideshow-information") !== "hide"; } catch (_error) { /* storage is optional */ }
    setInformationVisible(informationVisible);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeSlideshow, {once: true});
  } else {
    initializeSlideshow();
  }
}());
