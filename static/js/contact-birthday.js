(function (root) {
    "use strict";
    function valid(year, month, day) {
        const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
        const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        return year >= 1 && year <= 9999 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1];
    }

    function infer(partial, age, onDate) {
        const md = /^(?:--)?(\d\d)-(\d\d)$/.exec(partial);
        const on = /^(\d{4})-(\d\d)-(\d\d)$/.exec(onDate);
        if (!md || !on || !/^\d{1,3}$/.test(age) || Number(age) > 130) return "";
        const [, m, d] = md.map(Number);
        const [, year, month, day] = on.map(Number);
        if (!valid(year, month, day)) return "";
        // Never assume a legal/cultural Feb 28 vs March 1 convention.
        if (m === 2 && d === 29 && !valid(year, 2, 29) && ((month === 2 && day === 28) || (month === 3 && day === 1))) return "";
        const passed = month > m || (month === m && day >= d);
        const birthYear = year - Number(age) - (passed ? 0 : 1);
        return valid(birthYear, m, d) ? String(birthYear).padStart(4, "0") + "-" + md[1] + "-" + md[2] : "";
    }

    if (typeof module !== "undefined" && module.exports) module.exports = {infer};
    const card = root.document?.querySelector("[data-birthday-helper]");
    if (!card) return;
    const select = (key) => card.querySelector("[data-birthday-" + key + "]");
    const input = root.document.querySelector("#contact-birthday");
    const target = select("preview");
    const adopt = select("adopt");
    let proposal = "";
    select("calculate").addEventListener("click", () => {
        proposal = infer(select("month-day").value.trim(), select("age").value, select("reference").value);
        target.textContent = proposal || "Kein eindeutiges Geburtsdatum. Prüfe Alter, Stichtag sowie Tag und Monat.";
        adopt.disabled = !proposal;
    });
    for (const field of ["month-day", "age", "reference"])
        select(field).addEventListener("input", () => { proposal = ""; target.textContent = ""; adopt.disabled = true; });
    adopt.addEventListener("click", () => {
        if (proposal) {
            input.value = proposal;
            target.textContent = proposal + " übernommen. Speichere den Kontakt, um das Datum zu bestätigen.";
        }
    });
})(globalThis);
