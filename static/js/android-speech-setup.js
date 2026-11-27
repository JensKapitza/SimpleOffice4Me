(() => {
  "use strict";
  const form = document.querySelector("[data-android-speech-trial]");
  if (!form) return;
  const find = name => form.querySelector(`[data-android-speech-trial-${name}]`);
  const status = find("status"), audio = find("audio"), start = find("start"), stop = find("stop");
  const label = name => { status.textContent = form.dataset[name] || ""; };
  const endpoint = new URL(form.action, window.location.href);
  if (endpoint.origin !== window.location.origin || typeof fetch !== "function" ||
      typeof AbortController !== "function" || typeof URL.createObjectURL !== "function" ||
      typeof URL.revokeObjectURL !== "function" || typeof audio.play !== "function") {
    label("unsupported");
    return;
  }

  let active = null;
  function release(message = "") {
    const prior = active;
    active = null;
    if (prior) {
      prior.controller.abort();
      window.clearTimeout(prior.deadline);
      if (prior.url) {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
        URL.revokeObjectURL(prior.url);
      }
    }
    audio.hidden = true;
    stop.hidden = true;
    label(message);
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const body = new URLSearchParams(new FormData(form));
    release("loading");
    const current = {controller: new AbortController(), deadline: null, url: null};
    active = current;
    current.deadline = window.setTimeout(() => {
      if (active === current) release("failed");
    }, 30000);
    stop.hidden = false;
    try {
      const response = await fetch(endpoint.href, {
        method: "POST", body, credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: {Accept: "audio/wav"}, signal: current.controller.signal,
      });
      if (active !== current) return;
      const type = (response.headers.get("Content-Type") || "").split(";", 1)[0].trim().toLowerCase();
      if (!response.ok || !["audio/wav", "audio/x-wav"].includes(type)) {
        release("failed");
        return;
      }
      const wav = await response.blob();
      if (active !== current) return;
      if (!wav.size) {
        release("failed");
        return;
      }
      window.clearTimeout(current.deadline);
      current.url = URL.createObjectURL(wav);
      audio.src = current.url;
      audio.hidden = false;
      label("ready");
      try {
        await audio.play();
      } catch (_error) {
        // Browser autoplay rules may require a second click. Leave native Play
        // visible, but don't reinterpret a decode error as successful playback.
        if (active !== current) return;
        if (audio.error) release("failed");
      }
    } catch (_error) {
      if (active === current) release("failed");
    }
  });

  audio.addEventListener("playing", () => {
    if (!active?.url) return;
    label("playing");
    if (typeof CustomEvent === "function") {
      window.dispatchEvent(new CustomEvent("simpleoffice:speech-start", {detail: form}));
    }
  });
  audio.addEventListener("ended", () => { if (active?.url && audio.ended) release("ended"); });
  audio.addEventListener("error", () => { if (active?.url && audio.error) release("failed"); });
  stop.addEventListener("click", () => release("stopped"));
  window.addEventListener("pagehide", () => release());
  window.addEventListener("simpleoffice:speech-start", event => {
    if (event.detail !== form && active) release("stopped");
  });
  window.addEventListener("simpleoffice:speech-stop", () => {
    if (active) release("stopped");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && active) release("stopped");
  });
  start.disabled = false;
})();
