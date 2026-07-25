/* Translate static UI labels that are still present in legacy templates. */
(() => {
  const translations = window.SimpleOfficeTranslations || {};
  const entries = Object.entries(translations).sort(([a], [b]) => b.length - a.length);
  if (!entries.length) return;
  const replace = value => entries.reduce((text, [source, target]) => text.split(source).join(target), value);
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement && !["SCRIPT", "STYLE", "CODE", "PRE"].includes(node.parentElement.tagName) && node.nodeValue.trim()
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });
  const nodes = [];
  while (walk.nextNode()) nodes.push(walk.currentNode);
  nodes.forEach(node => { node.nodeValue = replace(node.nodeValue); });
  document.querySelectorAll("[placeholder],[title],[aria-label],input[type=submit]").forEach(element => {
    ["placeholder", "title", "aria-label"].forEach(attribute => {
      if (element.hasAttribute(attribute)) element.setAttribute(attribute, replace(element.getAttribute(attribute)));
    });
    if (element.matches("input[type=submit]")) element.value = replace(element.value);
  });
})();
