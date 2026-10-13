"use strict";
const form = document.querySelector("#connect");
const input = document.querySelector("#endpoint");
const error = document.querySelector("#error");
const savedEndpointKey = "simpleoffice.server-url";
function validateEndpoint(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error("Ungültige Serveradresse."); }
  if (url.username || url.password || url.search || url.hash) throw new Error("Nur die Serveradresse ohne Zugangsdaten oder Parameter angeben.");
  if (url.protocol !== "https:") throw new Error("Eine HTTPS-Verbindung wird benötigt.");
  if (!url.hostname || url.hostname === "tauri.localhost" || url.hostname.endsWith(".")) throw new Error("Ungültiger Hostname.");
  return url.origin + url.pathname.replace(/\/?$/, "/");
}
try {
  const saved = localStorage.getItem(savedEndpointKey);
  if (saved) input.value = validateEndpoint(saved);
} catch { /* An unavailable local preference must not prevent connecting. */ }
if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
  document.documentElement.dataset.bsTheme = "dark";
}
form.addEventListener("submit", (event) => {
  event.preventDefault();
  error.textContent = "";
  try {
    const target = validateEndpoint(input.value.trim());
    try { localStorage.setItem(savedEndpointKey, target); } catch { /* Connection still works without persistence. */ }
    window.location.assign(target);
  } catch (failure) {
    error.textContent = failure instanceof Error ? failure.message : "Ungültige Adresse.";
  }
});
