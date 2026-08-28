(() => {
  "use strict";

  function synchronize(form) {
    const invoiceToggle = form.querySelector("[data-object-invoice-toggle]");
    const categoryToggle = form.querySelector("[data-object-category-toggle]");
    const invoiceFields = form.querySelector("[data-object-invoice-fields]");
    const categoryFields = form.querySelector("[data-object-category-fields]");

    if (invoiceToggle && invoiceFields) {
      invoiceFields.hidden = !invoiceToggle.checked;
      invoiceToggle.setAttribute("aria-expanded", String(invoiceToggle.checked));
    }
    if (categoryToggle && categoryFields) {
      categoryFields.hidden = !categoryToggle.checked;
      categoryToggle.setAttribute("aria-expanded", String(categoryToggle.checked));
    }
  }

  document.querySelectorAll("[data-object-billing-form]").forEach((form) => {
    const update = () => synchronize(form);
    form.querySelector("[data-object-invoice-toggle]")?.addEventListener("change", update);
    form.querySelector("[data-object-category-toggle]")?.addEventListener("change", update);
    update();
  });
})();
