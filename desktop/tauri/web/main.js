"use strict";
const form = document.querySelector("#connect");
const input = document.querySelector("#endpoint");
const error = document.querySelector("#error");
function validateEndpoint(value) {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash) throw new Error("Nur die Serveradresse ohne Zugangsdaten oder Parameter angeben.");
  if (url.protocol !== "https:") throw new Error("Für den Tauri-Client wird eine HTTPS-Verbindung benötigt.");
  if (!url.hostname || url.hostname.endsWith(".")) throw new Error("Ungültiger Hostname.");
  return url.origin + "/";
}
form.addEventListener("submit", (event) => {
  event.preventDefault();
  error.textContent = "";
  try {
    const target = validateEndpoint(input.value.trim());
    // No IPC, shell, or filesystem permissions are granted to remote pages.
    window.location.assign(target);
  } catch (failure) {
    error.textContent = failure instanceof Error ? failure.message : "Ungültige Adresse.";
  }
});
