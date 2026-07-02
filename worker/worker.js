/*
 * Johor Election App — crowd-sourced coalition labels + community forecast API.
 * Cloudflare Worker port of ../server.js (same validation rules).
 *
 * Storage: a KV namespace bound as TAGS; JSON documents under keys
 * "tags" (coalition labels) and "forecast" (SE-16 seat predictions).
 * Deploy:  see ../DEPLOY.md  (wrangler login && wrangler deploy)
 *
 * Routes:
 *   GET  /api/tags                 -> { CODE: [ {id,label,votes} ], ... }
 *   POST /api/tags  {code,label}   -> add a label (idempotent on same text)
 *   POST /api/vote  {code,id,dir}  -> dir +1/-1
 *   GET  /api/forecast             -> { seats:{ "N.01":{BN:n,…} }, n:total }
 *   POST /api/predict {seat,coalition}  -> record one SE-16 seat prediction
 */

const MAX_LABEL_LEN   = 40;
const MAX_TAGS_PER_CO = 12;
const MAX_BODY_BYTES  = 4096;
const CODE_RE         = /^[A-Za-z0-9]{1,16}$/;
const COOLDOWN_SECS   = 5;          // per-IP write cooldown
/* Forecast: SE-16 has seats N.01–N.56; predictions are per-coalition only
   (never per named candidate — deliberate ethical boundary). */
const SEAT_RE         = /^N\.(\d{2})$/;
const SEAT_MAX        = 56;
const FORECAST_COALS  = ["BN", "PH", "PN", "ALONE", "OTHER"];
const PREDICT_DAILY_CAP = 80;       // per IP per day (56 seats + slack)

/* Origins allowed to call the API. Add your Pages origin here. */
const ALLOWED_ORIGINS = [
  "https://electionnuances.github.io",
  "http://localhost:5173",
  "http://127.0.0.1:5173",
];

/* Seed mirrors data/coalition_labels.json — only data-verified names.
   HAK stays empty on purpose: its 1986 name is genuinely uncertain. */
const SEED = {
  BN:["Barisan Nasional"], PH:["Pakatan Harapan"], PN:["Perikatan Nasional"],
  PERIKATAN:["The Alliance (Perikatan)"], PR:["Pakatan Rakyat"],
  BA:["Barisan Alternatif"], APU:["Angkatan Perpaduan Ummah"],
  GS:["Gagasan Sejahtera"], SF:["Socialist Front"], GR:["Gagasan Rakyat"],
  ALONE:["Independent"], HAK:[],
};

function corsHeaders(request){
  const origin = request.headers.get("Origin") || "";
  const allow = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Content-Type": "application/json",
  };
}
const json = (request, code, obj) =>
  new Response(JSON.stringify(obj), { status: code, headers: corsHeaders(request) });

function cleanLabel(s){
  if (typeof s !== "string") return null;
  const t = s.replace(/[\u0000-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim();
  if (!t || t.length > MAX_LABEL_LEN) return null;
  return t;
}

async function loadStore(env){
  const raw = await env.TAGS.get("tags");
  if (raw) { try { return JSON.parse(raw); } catch {} }
  const seeded = {};
  for (const code in SEED)
    seeded[code] = SEED[code].map(label => ({ id: crypto.randomUUID(), label, votes: 1 }));
  await env.TAGS.put("tags", JSON.stringify(seeded));
  return seeded;
}
const saveStore = (env, store) => env.TAGS.put("tags", JSON.stringify(store));

/* Per-IP write cooldown via a KV TTL key (best-effort; KV is eventually consistent). */
async function cooledDown(env, request){
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = "cool:" + ip;
  if (await env.TAGS.get(key)) return false;
  await env.TAGS.put(key, "1", { expirationTtl: COOLDOWN_SECS });
  return true;
}

async function readBody(request){
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) throw new Error("body too large");
  return text ? JSON.parse(text) : {};
}

/* ---- community forecast (SE-16 seat predictions) ---- */
async function loadForecast(env){
  const raw = await env.TAGS.get("forecast");
  if (raw) { try { return JSON.parse(raw); } catch {} }
  return { seats: {}, n: 0 };
}

function validSeat(seat){
  const m = SEAT_RE.exec(seat || "");
  if (!m) return false;
  const n = parseInt(m[1], 10);
  return n >= 1 && n <= SEAT_MAX;
}

/* Per-IP daily cap for predictions (separate from the write cooldown). */
async function underDailyCap(env, request){
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = "predcap:" + ip;
  const used = parseInt((await env.TAGS.get(key)) || "0", 10);
  if (used >= PREDICT_DAILY_CAP) return false;
  await env.TAGS.put(key, String(used + 1), { expirationTtl: 86400 });
  return true;
}

async function handlePredict(request, env){
  if (!(await underDailyCap(env, request))) return json(request, 429, { error: "daily prediction limit reached" });
  const body = await readBody(request);
  const { seat, coalition } = body;
  if (!validSeat(seat)) return json(request, 400, { error: "invalid seat" });
  if (!FORECAST_COALS.includes(coalition)) return json(request, 400, { error: "invalid coalition" });
  const fc = await loadForecast(env);
  const s = fc.seats[seat] || (fc.seats[seat] = {});
  s[coalition] = (s[coalition] || 0) + 1;
  fc.n += 1;
  await env.TAGS.put("forecast", JSON.stringify(fc));
  return json(request, 200, { seat, counts: s, n: fc.n });
}

export default {
  async fetch(request, env){
    const url = new URL(request.url);
    try {
      if (request.method === "OPTIONS")
        return new Response(null, { status: 204, headers: corsHeaders(request) });

      if (request.method === "GET" && url.pathname === "/api/tags")
        return json(request, 200, await loadStore(env));

      if (request.method === "GET" && url.pathname === "/api/forecast")
        return json(request, 200, await loadForecast(env));

      if (request.method === "POST" && url.pathname === "/api/predict")
        return handlePredict(request, env);

      if (request.method === "POST" && url.pathname === "/api/tags"){
        if (!(await cooledDown(env, request))) return json(request, 429, { error: "slow down" });
        const body = await readBody(request);
        const code = body.code;
        const label = cleanLabel(body.label);
        if (!CODE_RE.test(code || "") || !label) return json(request, 400, { error: "invalid code or label" });
        const store = await loadStore(env);
        const list = store[code] || (store[code] = []);
        if (list.length >= MAX_TAGS_PER_CO) return json(request, 409, { error: "tag limit reached" });
        if (list.some(t => t.label.toLowerCase() === label.toLowerCase()))
          return json(request, 200, { code, tags: list });
        const tag = { id: crypto.randomUUID(), label, votes: 1 };
        list.push(tag);
        await saveStore(env, store);
        return json(request, 201, { code, tags: list, added: tag.id });
      }

      if (request.method === "POST" && url.pathname === "/api/vote"){
        if (!(await cooledDown(env, request))) return json(request, 429, { error: "slow down" });
        const body = await readBody(request);
        const { code, id } = body;
        const dir = body.dir === -1 ? -1 : 1;
        const store = await loadStore(env);
        if (!CODE_RE.test(code || "") || !store[code]) return json(request, 400, { error: "unknown coalition" });
        const tag = store[code].find(t => t.id === id);
        if (!tag) return json(request, 404, { error: "unknown tag" });
        tag.votes = Math.max(0, tag.votes + dir);
        await saveStore(env, store);
        return json(request, 200, { code, tags: store[code] });
      }

      return json(request, 404, { error: "not found" });
    } catch (e) {
      return json(request, 400, { error: e.message || "bad request" });
    }
  },
};
