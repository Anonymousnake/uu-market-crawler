const fs = require("fs");
const http = require("http");

const OUTPUT_PATH = "D:/Codex/uu-market-crawler/watchlist.discovered.json";

const TARGETS = [
  ["Recoil Case", "case"],
  ["Snakebite Case", "case"],
  ["Fracture Case", "case"],
  ["Kilowatt Case", "case"],
  ["Gallery Case", "case"],
  ["Revolution Case", "case"],
  ["Dreams & Nightmares Case", "case"],
  ["Operation Riptide Case", "case"],
  ["Operation Broken Fang Case", "case"],
  ["Prisma 2 Case", "case"],
  ["Prisma Case", "case"],
  ["CS20 Case", "case"],
  ["Clutch Case", "case"],
  ["Horizon Case", "case"],
  ["Danger Zone Case", "case"],
  ["Spectrum 2 Case", "case"],
  ["Spectrum Case", "case"],
  ["Glove Case", "case"],
  ["Gamma 2 Case", "case"],
  ["Gamma Case", "case"],
  ["Chroma 3 Case", "case"],
  ["Chroma 2 Case", "case"],
  ["Chroma Case", "case"],
  ["Operation Wildfire Case", "case"],
  ["Revolver Case", "case"],
  ["Shadow Case", "case"],
  ["Falchion Case", "case"],
  ["Operation Vanguard Weapon Case", "case"],
  ["Operation Breakout Weapon Case", "case"],
  ["Huntsman Weapon Case", "case"],
  ["Operation Phoenix Weapon Case", "case"],
  ["Winter Offensive Weapon Case", "case"],
  ["eSports 2013 Case", "case"],
  ["eSports 2013 Winter Case", "case"],
  ["eSports 2014 Summer Case", "case"],
  ["CS:GO Weapon Case", "case"],
  ["CS:GO Weapon Case 2", "case"],
  ["CS:GO Weapon Case 3", "case"],
  ["Budapest 2025 Contenders Sticker Capsule", "capsule"],
  ["Budapest 2025 Legends Sticker Capsule", "capsule"],
  ["Budapest 2025 Challengers Sticker Capsule", "capsule"],
  ["Austin 2025 Contenders Sticker Capsule", "capsule"],
  ["Austin 2025 Legends Sticker Capsule", "capsule"],
  ["Austin 2025 Challengers Sticker Capsule", "capsule"],
  ["Shanghai 2024 Contenders Sticker Capsule", "capsule"],
  ["Shanghai 2024 Legends Sticker Capsule", "capsule"],
  ["Shanghai 2024 Challengers Sticker Capsule", "capsule"],
  ["Copenhagen 2024 Contenders Sticker Capsule", "capsule"],
  ["Copenhagen 2024 Legends Sticker Capsule", "capsule"],
  ["Copenhagen 2024 Challengers Sticker Capsule", "capsule"],
  ["Paris 2023 Contenders Sticker Capsule", "capsule"],
  ["Paris 2023 Legends Sticker Capsule", "capsule"],
  ["Paris 2023 Challengers Sticker Capsule", "capsule"],
  ["Rio 2022 Contenders Sticker Capsule", "capsule"],
  ["Rio 2022 Legends Sticker Capsule", "capsule"],
  ["Rio 2022 Challengers Sticker Capsule", "capsule"],
  ["Antwerp 2022 Contenders Sticker Capsule", "capsule"],
  ["Antwerp 2022 Legends Sticker Capsule", "capsule"],
  ["Antwerp 2022 Challengers Sticker Capsule", "capsule"],
  ["Stockholm 2021 Contenders Sticker Capsule", "capsule"],
  ["Stockholm 2021 Legends Sticker Capsule", "capsule"],
  ["Stockholm 2021 Challengers Sticker Capsule", "capsule"],
];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    http
      .get(url, (res) => {
        let data = "";
        res.on("data", (chunk) => {
          data += chunk;
        });
        res.on("end", () => {
          resolve(JSON.parse(data));
        });
      })
      .on("error", reject);
  });
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    let id = 0;

    ws.onopen = () => {
      resolve({
        send(method, params = {}) {
          return new Promise((res, rej) => {
            const msgId = ++id;
            pending.set(msgId, { res, rej });
            ws.send(JSON.stringify({ id: msgId, method, params }));
          });
        },
        on(fn) {
          ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.id && pending.has(msg.id)) {
              const callbacks = pending.get(msg.id);
              pending.delete(msg.id);
              if (msg.error) {
                callbacks.rej(new Error(JSON.stringify(msg.error)));
              } else {
                callbacks.res(msg.result);
              }
            } else {
              fn(msg);
            }
          };
        },
        close() {
          ws.close();
        },
      });
    };
    ws.onerror = reject;
  });
}

function pickHeaders(headers) {
  const names = [
    "secret-v",
    "authorization",
    "deviceId",
    "appType",
    "AppVersion",
    "App-Version",
    "deviceUk",
    "platform",
    "uk",
    "Referer",
    "User-Agent",
    "Accept",
    "Content-Type",
    "Origin",
  ];
  const out = {};
  for (const name of names) {
    const found = Object.keys(headers || {}).find(
      (key) => key.toLowerCase() === name.toLowerCase(),
    );
    if (found) out[name] = headers[found];
  }
  out.Accept = "application/json, text/plain, */*";
  out["Content-Type"] = "application/json";
  out.Origin = "https://youpin898.com";
  out.Referer = "https://youpin898.com/";
  return out;
}

function normalize(query, kind, row) {
  return {
    query,
    kind,
    template_id: row.id,
    sort_id: row.sortId,
    name: row.commodityName,
    hash_name: row.commodityHashName,
    price: row.price,
    steam_price: row.steamPrice,
    steam_usd_price: row.steamUsdPrice,
    on_sale_count: row.onSaleCount,
    on_lease_count: row.onLeaseCount,
    type_name: row.typeName,
    rarity: row.rarity,
    quality: row.quality,
    list_type: row.listType,
    icon_url: row.iconUrl,
    icon_url_large: row.iconUrlLarge,
  };
}

async function captureMarketHeaders() {
  const pages = await getJson("http://127.0.0.1:9222/json/list");
  const page = pages.find((item) => item.url && item.url.includes("youpin898.com/market"));
  if (!page) throw new Error("No UU market page found on http://127.0.0.1:9222");

  const cdp = await connect(page.webSocketDebuggerUrl);
  let capturedHeaders = null;
  cdp.on((msg) => {
    if (
      msg.method === "Network.requestWillBeSent" &&
      msg.params.request.url.includes("querySaleTemplate")
    ) {
      capturedHeaders = pickHeaders(msg.params.request.headers);
    }
  });
  await cdp.send("Network.enable");
  await cdp.send("Runtime.evaluate", {
    expression: `(() => { const input=document.querySelector('input[type="search"]'); if (input) input.focus(); })()`,
  });
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    windowsVirtualKeyCode: 65,
    modifiers: 2,
    key: "a",
    code: "KeyA",
  });
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    windowsVirtualKeyCode: 65,
    modifiers: 2,
    key: "a",
    code: "KeyA",
  });
  await cdp.send("Input.insertText", { text: "Recoil Case" });
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    windowsVirtualKeyCode: 13,
    key: "Enter",
    code: "Enter",
  });
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    windowsVirtualKeyCode: 13,
    key: "Enter",
    code: "Enter",
  });

  for (let i = 0; i < 20 && !capturedHeaders; i += 1) {
    await sleep(250);
  }
  cdp.close();
  if (!capturedHeaders) throw new Error("Failed to capture querySaleTemplate headers");
  return capturedHeaders;
}

async function queryTemplate(headers, query, kind) {
  const payload = {
    listSortType: 0,
    sortType: 0,
    keyWords: query,
    pageSize: 20,
    pageIndex: 1,
  };
  const response = await fetch(
    "https://api.youpin898.com/api/homepage/pc/goods/market/querySaleTemplate",
    {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    },
  );
  if (response.status === 403 || response.status === 429) {
    throw new Error(`Stop on status ${response.status} while querying ${query}`);
  }
  const body = await response.json();
  const rows = Array.isArray(body.Data) ? body.Data : [];
  const exact =
    rows.find(
      (row) =>
        String(row.commodityHashName || "").toLowerCase() === query.toLowerCase() ||
        String(row.commodityName || "").toLowerCase() === query.toLowerCase(),
    ) || rows[0];
  return exact
    ? { result: normalize(query, kind, exact), miss: null }
    : {
        result: null,
        miss: {
          query,
          kind,
          code: body.Code,
          msg: body.Msg,
          total: body.TotalCount,
        },
      };
}

async function main() {
  const headers = await captureMarketHeaders();
  const results = [];
  const misses = [];
  for (const [query, kind] of TARGETS) {
    const { result, miss } = await queryTemplate(headers, query, kind);
    if (result) results.push(result);
    if (miss) misses.push(miss);
    await sleep(1200 + Math.floor(Math.random() * 1200));
  }
  const output = {
    generated_at: new Date().toISOString(),
    source: "querySaleTemplate keyWords",
    count: results.length,
    results,
    misses,
  };
  fs.writeFileSync(OUTPUT_PATH, JSON.stringify(output, null, 2), "utf8");
  console.log(
    JSON.stringify(
      {
        saved: OUTPUT_PATH,
        count: results.length,
        misses: misses.length,
        first: results.slice(0, 5),
        missesSample: misses.slice(0, 10),
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
