(() => {
  "use strict";

  const mutationMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
  const xhrMeta = new WeakMap();

  const currentToken = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
  const methodIsMutation = (method) => mutationMethods.has(String(method || "GET").toUpperCase());
  const isSameOrigin = (url) => {
    try {
      return new URL(url || window.location.href, window.location.href).origin === window.location.origin;
    } catch (_error) {
      return false;
    }
  };

  const formIntent = (form, submitter = null) => ({
    method: String(submitter?.getAttribute("formmethod") || form.getAttribute("method") || "GET").toUpperCase(),
    action: submitter?.getAttribute("formaction") || form.getAttribute("action") || window.location.href,
  });

  const addToken = (form, submitter = null) => {
    if (!(form instanceof HTMLFormElement)) return;
    const token = currentToken();
    if (!token) return;
    const intent = formIntent(form, submitter);
    if (!methodIsMutation(intent.method) || !isSameOrigin(intent.action)) return;
    let field = form.querySelector('input[name="_csrf_token"]');
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "_csrf_token";
      field.dataset.simpleofficeCsrf = "generated";
      form.append(field);
    }
    field.value = token;
  };

  document.querySelectorAll("form").forEach((form) => addToken(form));
  document.addEventListener("submit", (event) => {
    if (event.target instanceof HTMLFormElement) addToken(event.target, event.submitter || null);
  }, true);

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (!(node instanceof Element)) return;
      if (node instanceof HTMLFormElement) addToken(node);
      node.querySelectorAll?.("form").forEach((form) => addToken(form));
    }));
  });
  observer.observe(document.documentElement, {childList: true, subtree: true});

  const originalFetch = window.fetch.bind(window);
  window.fetch = (resource, options = {}) => {
    const request = resource instanceof Request ? resource : null;
    const url = new URL(request ? request.url : resource, window.location.href);
    const method = String(options.method || request?.method || "GET").toUpperCase();
    const token = currentToken();
    if (!token || !isSameOrigin(url.href) || !methodIsMutation(method)) {
      return originalFetch(resource, options);
    }
    const headers = new Headers(options.headers || request?.headers || undefined);
    headers.set("X-CSRF-Token", token);
    return originalFetch(resource, {...options, headers});
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    xhrMeta.set(this, {method: String(method || "GET").toUpperCase(), url: String(url || "")});
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    const meta = xhrMeta.get(this);
    const token = currentToken();
    if (meta && token && methodIsMutation(meta.method) && isSameOrigin(meta.url)) {
      this.setRequestHeader("X-CSRF-Token", token);
    }
    return originalSend.apply(this, args);
  };
})();
