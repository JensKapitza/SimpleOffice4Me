const {test} = require("node:test");
const assert = require("node:assert/strict");
const {infer} = require("../static/js/contact-birthday.js");

test("birthday year uses the explicit age reference date", () => {
    assert.equal(infer("08-31", "40", "2026-08-31"), "1986-08-31");
    assert.equal(infer("--08-31", "40", "2026-08-30"), "1985-08-31");
    assert.equal(infer("12-31", "30", "2026-01-01"), "1995-12-31");
});

test("unknown month/day and invalid or ambiguous leap dates stay unknown", () => {
    assert.equal(infer("", "40", "2026-08-30"), "");
    assert.equal(infer("02-29", "22", "2026-02-28"), "");
    assert.equal(infer("02-29", "22", "2026-03-02"), "2004-02-29");
    assert.equal(infer("02-29", "21", "2026-03-02"), "");
    assert.equal(infer("06-31", "40", "2026-08-30"), "");
});
