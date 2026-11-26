/* Translate static UI labels that are still present in legacy templates. */
(() => {
  const language = document.documentElement.lang === "de" ? "de" : "en";
  window.SimpleOfficeI18n = Object.freeze({
    language,
    text(german, english) { return language === "de" ? german : english; },
  });

  const translations = window.SimpleOfficeTranslations || {};
  const entries = Object.entries(translations).sort(([a], [b]) => b.length - a.length);
  if (!entries.length) return;
  const replace = value => entries.reduce((text, [source, target]) => text.split(source).join(target), value);
  // Literal UI translations must not alter an explicitly marked name,
  // path, or other user data. Honor the nearest HTML translation setting.
  const canTranslate = element => !element.closest('[translate="no" i],[translate="yes" i],[translate=""]')
    ?.getAttribute('translate')?.match(/^no$/i);
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement && !["SCRIPT", "STYLE", "CODE", "PRE"].includes(node.parentElement.tagName) && canTranslate(node.parentElement) && node.nodeValue.trim()
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });
  const nodes = [];
  while (walk.nextNode()) nodes.push(walk.currentNode);
  nodes.forEach(node => { node.nodeValue = replace(node.nodeValue); });
  document.querySelectorAll("[placeholder],[title],[aria-label],input[type=submit]").forEach(element => {
    if (!canTranslate(element)) return;
    ["placeholder", "title", "aria-label"].forEach(attribute => {
      if (element.hasAttribute(attribute)) element.setAttribute(attribute, replace(element.getAttribute(attribute)));
    });
    if (element.matches("input[type=submit]")) element.value = replace(element.value);
  });
})();
