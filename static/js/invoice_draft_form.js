(() => {
  "use strict";
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
    row.querySelector(".object-id").textContent = line.object_display_id ? `#${line.object_display_id}` : "frei";
    row.querySelector(".object-name").textContent = line.object_name || "Freie Rechnungsposition";
    row.querySelector(".line-description").value = line.description || "";
    row.querySelector(".line-qty").value = line.quantity || "1";
    row.querySelector(".line-net").value = line.net_unit_price || "0.00";
    row.querySelector(".line-vat").value = line.vat_rate || "0";
    row.querySelectorAll("input").forEach(input => input.addEventListener("input", recalculate));
    row.querySelector(".remove-line").addEventListener("click", () => { row.remove(); recalculate(); });
    body.append(row);
  });
  recalculate();
})();
