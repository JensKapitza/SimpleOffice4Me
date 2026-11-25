(() => {
  "use strict";

  const root = document.querySelector("[data-book-scanner]");
  if (!root) return;
  const form = root.querySelector("form");
  const controls = form.querySelector("[data-scan-controls]");
  const submit = form.querySelector("[data-scan-download]");
  const list = form.querySelector("[data-scan-pages]");
  const message = form.querySelector("[data-scan-message]");
  const inputs = form.querySelectorAll("[data-pick-pages]");
  const en = root.dataset.lang === "en";
  const pages = [];
  let busy = false;

  const t = (de, translation) => en ? translation : de;
  const say = (text) => { message.textContent = text; };

  function button(label, action, disabled = false) {
    const control = document.createElement("button");
    control.type = "button";
    control.className = "btn btn-sm btn-outline-secondary";
    control.textContent = label;
    control.disabled = disabled;
    control.addEventListener("click", action);
    return control;
  }

  function move(index, direction) {
    const other = index + direction;
    [pages[index], pages[other]] = [pages[other], pages[index]];
    render(other);
    list.children[other]?.querySelector("button:not([disabled])")?.focus();
  }

  function remove(index) {
    const [removed] = pages.splice(index, 1);
    URL.revokeObjectURL(removed.preview);
    render(index);
    const next = Math.min(index, pages.length - 1);
    if (next >= 0) list.children[next]?.querySelector("button:not([disabled])")?.focus();
    else inputs[0].focus();
  }

  function render(index = null) {
    list.replaceChildren();
    for (const [position, page] of pages.entries()) {
      const row = document.createElement("li");
      row.className = "list-group-item";
      const label = document.createElement("span");
      label.className = "ms-2";
      label.textContent = page.file.name;
      const preview = document.createElement("img");
      preview.src = page.preview;
      preview.alt = "";
      preview.className = "d-block my-2 object-fit-contain";
      preview.width = 112;
      preview.height = 112;
      preview.style.transform = `rotate(${page.turns * 90}deg)`;
      const edit = document.createElement("div");
      edit.className = "d-flex flex-wrap gap-2";
      const rotate = button(t("90° drehen", "Rotate 90°"), () => {
        page.turns = (page.turns + 1) % 4;
        preview.style.transform = `rotate(${page.turns * 90}deg)`;
        say(t(`Seite ${position + 1}: ${page.turns * 90}°.`, `Page ${position + 1}: ${page.turns * 90}°.`));
      });
      edit.append(
        button(t("Nach oben", "Move up"), () => move(position, -1), position === 0),
        button(t("Nach unten", "Move down"), () => move(position, 1), position === pages.length - 1),
        rotate,
        button(t("Entfernen", "Remove"), () => remove(position)),
      );
      row.append(label, preview, edit);
      list.append(row);
    }
    submit.disabled = busy || pages.length === 0;
    if (index !== null) {
      const count = pages.length;
      say(t(`${count} Seite(n) ausgewählt.`, `${count} page(s) selected.`));
    }
  }

  function add(files) {
    const batch = Array.from(files);
    const all = [...pages.map((page) => page.file), ...batch];
    if (all.length > Number(root.dataset.maxPages)
        || all.some((file) => file.size > Number(root.dataset.maxPageBytes))
        || all.reduce((size, file) => size + file.size, 0) >= Number(root.dataset.maxTotalBytes)) {
      say(t("Diese Auswahl ist zu groß. Bitte weniger oder kleinere Seiten auswählen.",
        "This selection is too large. Select fewer or smaller pages."));
      return;
    }
    pages.push(...batch.map((file) => ({file, preview: URL.createObjectURL(file), turns: 0})));
    render(0);
  }

  for (const input of inputs) {
    input.addEventListener("change", () => { add(input.files); input.value = ""; });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!pages.length || busy) return;
    const body = new FormData(form);
    const order = [];
    pages.forEach((page, index) => { body.append("pages", page.file); order.push([index, page.turns]); });
    body.set("order", JSON.stringify(order));
    busy = true;
    controls.disabled = true;
    say(t("PDF wird erstellt…", "Creating PDF…"));
    const failure = t("Das PDF konnte nicht erstellt werden. Bitte Auswahl oder Anmeldung prüfen.",
      "The PDF could not be created. Check your selection or sign-in.");
    try {
      const response = await fetch(form.action, {method: "POST", body, credentials: "same-origin", redirect: "error"});
      const type = response.headers.get("Content-Type") || "";
      if (!response.ok || !type.startsWith("application/pdf")) {
        const detail = type.startsWith("application/json") ? await response.json() : {};
        say(typeof detail.error === "string" ? detail.error : failure);
        return;
      }
      const href = URL.createObjectURL(await response.blob());
      const download = document.createElement("a");
      download.href = href;
      download.download = t("Buchscan.pdf", "Book-scan.pdf");
      root.append(download);
      download.click();
      download.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 1500);
      say(t("PDF erstellt. Nichts wurde importiert oder geteilt.",
        "PDF created. Nothing was imported or shared."));
    } catch (_error) {
      say(failure);
    } finally {
      busy = false;
      controls.disabled = false;
      submit.disabled = pages.length === 0;
    }
  });

  controls.disabled = false;
})();
