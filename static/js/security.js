(() => {
  "use strict";
  const token = document.querySelector('meta[name="csrf-token"]')?.content || "";
  if (!token) return;

  const addToken = (form) => {
    const method = (form.method || "get").toUpperCase();
    if (!new Set(["POST", "PUT", "PATCH", "DELETE"]).has(method)) return;
    let field = form.querySelector('input[name="_csrf_token"]');
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "_csrf_token";
      form.append(field);
    }
    field.value = token;
  };

  document.querySelectorAll("form").forEach(addToken);
  document.addEventListener("submit", (event) => addToken(event.target), true);

  const originalFetch = window.fetch.bind(window);
  window.fetch = (resource, options = {}) => {
    const url = new URL(resource instanceof Request ? resource.url : resource, window.location.href);
    const method = String(options.method || (resource instanceof Request ? resource.method : "GET")).toUpperCase();
    if (url.origin !== window.location.origin || !["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      return originalFetch(resource, options);
    }
    const headers = new Headers(options.headers || (resource instanceof Request ? resource.headers : undefined));
    headers.set("X-CSRF-Token", token);
    return originalFetch(resource, {...options, headers});
  };
})();
