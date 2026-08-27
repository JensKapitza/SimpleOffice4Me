(() => {
  "use strict";
  const recipientType = document.getElementById("recipient-type");
  const companyGroup = document.getElementById("recipient-company-group");
  const company = document.getElementById("recipient-company");
  const contact = document.getElementById("recipient-contact");
  const contactLabel = document.getElementById("recipient-contact-label");
  if (recipientType && companyGroup && company && contact) {
    const updateRecipient = () => {
      const isCompany = recipientType.value === "company";
      companyGroup.hidden = !isCompany;
      company.required = isCompany;
      contact.required = !isCompany;
      if (contactLabel) contactLabel.textContent = isCompany ? contactLabel.dataset.company : contactLabel.dataset.private;
    };
    recipientType.addEventListener("change", updateRecipient);
    updateRecipient();
  }
  const data = document.getElementById("invoice-draft-lines");
  const body = document.querySelector("#invoice-lines tbody");
  const template = document.getElementById("invoice-line-template");
  if (!data || !body || !template) return;

  const lines = JSON.parse(data.textContent || "[]");
  const number = value => Number(String(value || "0").replace(",", ".")) || 0;
  const format = value => `${value.toFixed(2).replace(".", ",")} €`;
  const recalculate = () => {
    let net = 0;
    let tax = 0;
    body.querySelectorAll("tr").forEach(row => {
      const line = number(row.querySelector(".line-qty").value) * number(row.querySelector(".line-net").value);
      net += line;
      tax += line * number(row.querySelector(".line-vat").value) / 100;
      row.querySelector(".line-total").textContent = format(line);
    });
    document.getElementById("empty-lines").hidden = body.children.length > 0;
    document.getElementById("sum-net").textContent = format(net);
    document.getElementById("sum-tax").textContent = format(tax);
    document.getElementById("sum-gross").textContent = format(net + tax);
  };

  lines.forEach(line => {
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector('[name="line_object_id"]').value = line.object_id || "";
    row.querySelector('[name="line_project_id"]').value = line.project_id || "";
    row.querySelector('[name="line_source_type"]').value = line.project_source_type || "";
    row.querySelector('[name="line_source_id"]').value = line.project_source_id || "";
    row.querySelector(".object-id").textContent = line.object_display_id ? `#${line.object_display_id}` : "frei";
    row.querySelector(".object-name").textContent = line.object_name || "Freie Rechnungsposition";
    row.querySelector(".line-description").value = line.description || "";
    row.querySelector(".line-category").value = line.category || "";
    row.querySelector(".line-qty").value = line.quantity || "1";
    row.querySelector(".line-net").value = line.net_unit_price || "0.00";
    row.querySelector(".line-vat").value = line.vat_rate || "0";
    row.querySelectorAll("input").forEach(input => input.addEventListener("input", recalculate));
    row.querySelector(".remove-line").addEventListener("click", () => { row.remove(); recalculate(); });
    body.append(row);
  });
  recalculate();
})();
