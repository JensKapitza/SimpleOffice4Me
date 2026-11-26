/* Render the actual served PC page and JavaScript in jsdom. HTTPS is real. */
"use strict";
const {createInterface} = require("node:readline");
const {webcrypto} = require("node:crypto");
const {JSDOM} = require("jsdom");

const stdin = createInterface({input:process.stdin})[Symbol.asyncIterator]();
const wait = async (condition) => {
  const limit = Date.now() + 12500;
  while (Date.now() < limit) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 60));
  }
  throw new Error("rendered PC view did not meet " + condition);
};
const mark = async (event, fingerprint) => {
  process.stdout.write(JSON.stringify({event, fingerprint}) + "\n");
  if (event !== "revoked" && (await stdin.next()).value !== "ok") {
    throw new Error("fixture did not acknowledge " + event);
  }
};

(async () => {
  const {value} = await stdin.next();
  const {url, cookie} = JSON.parse(value);
  if (!/^https:\/\/127\.0\.0\.1:\d+\/android\/pc-folders$/.test(url)) throw new Error("loopback HTTPS required");
  const origin = new URL(url).origin;
  const realFetch = (target, options) => {
    const link = new URL(target, url);
    if (link.origin !== origin) throw new Error("fixture request escaped local TLS");
    return fetch(link, {...options, headers:{...options?.headers, Cookie:cookie}, redirect:"error"});
  };
  const page = await realFetch(url);
  if (!page.ok || page.headers.get("cache-control") !== "private, no-store") throw new Error("private page unavailable");
  const dom = new JSDOM(await page.text(), {runScripts:"outside-only", url, pretendToBeVisual:true});
  try {
    const {window} = dom;
    Object.defineProperties(window, {
      crypto:{value:webcrypto}, isSecureContext:{value:true},
      fetch:{value:realFetch}, TextEncoder:{value:TextEncoder}, AbortSignal:{value:AbortSignal},
    });
    const doc = window.document;
    const script = [...doc.scripts].find((item) => item.src.endsWith("js/android-paired-folder-pc.js"));
    if (!script) throw new Error("could not find the product JS");
    const served = await realFetch(script.src);
    if (!served.ok) throw new Error("product JS could not be loaded");
    window.eval(await served.text());
    const byId = (id) => doc.getElementById("paired-folder-" + id);
    const search = byId("search"), output = byId("code"), results = byId("results");
    byId("pair").click();
    await wait(() => output.textContent.replaceAll(" ", "").length === 64);
    if (!search.hidden) throw new Error("search visible before consent");
    await mark("requested", output.textContent.replaceAll(" ", ""));
    await wait(() => /separat/i.test(byId("state").textContent));
    if (!search.hidden) throw new Error("pairing alone enabled search");
    await mark("paired");
    await wait(() => !search.hidden);
    byId("query").value = "bleujay";
    search.requestSubmit();
    await wait(() => results.textContent.includes("Invoice BLEUJAY.pdf"));
    if (results.querySelectorAll("li").length !== 1 || results.textContent.includes("PRIVATE")
        || results.querySelector("a")) throw new Error("unexpected result scope");
    await mark("matched");
    await wait(() => search.hidden && !results.textContent && /separat/i.test(byId("state").textContent));
    await mark("unshared");
    await wait(() => !search.hidden && !results.textContent);
    byId("query").value = "bleujay";
    search.requestSubmit();
    await wait(() => results.textContent.includes("Invoice BLEUJAY.pdf"));
    await mark("reshared");
    await wait(() => search.hidden && !results.textContent && !byId("pair").disabled);
    await mark("revoked");
  } finally {
    dom.window.close();
    await stdin.return();
    process.stdin.pause();
  }
})().catch((error) => { console.error(error.message); process.exitCode = 1; });
