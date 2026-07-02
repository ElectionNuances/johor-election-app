#!/usr/bin/env node
/*
 * Johor DUN map — tiny shared backend for crowd-sourced coalition labels.
 * Zero dependencies (Node built-ins only). Run:  node server.js
 * Then open http://localhost:5173
 *
 * Serves index.html + ./data/* and a small JSON API backed by tags.json:
 *   GET  /api/tags            -> { CODE: [ {id, label, votes} ], ... }
 *   POST /api/tags  {code,label}      -> add a label, returns that coalition's tags
 *   POST /api/vote  {code,id,dir}     -> dir +1/-1, returns that coalition's tags
 */
const http = require("http");
const fs   = require("fs");
const path = require("path");
const crypto = require("crypto");

const PORT      = process.env.PORT || 5173;
const ROOT      = __dirname;
const HTML_FILE = path.join(ROOT, "index.html");
const STORE     = path.join(ROOT, "tags.json");

/* ---- limits / validation ---- */
const MAX_LABEL_LEN   = 40;
const MAX_TAGS_PER_CO = 12;
const MAX_BODY_BYTES  = 4096;
const CODE_RE         = /^[A-Za-z0-9]{1,16}$/;

/* Seed only data-verified names; leave genuinely-uncertain codes (HAK) empty. */
const SEED = {
  BN:["Barisan Nasional"], PH:["Pakatan Harapan"], PN:["Perikatan Nasional"],
  PERIKATAN:["The Alliance (Perikatan)"], PR:["Pakatan Rakyat"],
  BA:["Barisan Alternatif"], APU:["Angkatan Perpaduan Ummah"],
  GS:["Gagasan Sejahtera"], SF:["Socialist Front"], GR:["Gagasan Rakyat"],
  ALONE:["Independent"], HAK:[],
};

/* ---- store (in memory, persisted atomically) ---- */
let store = load();

function load(){
  try {
    return JSON.parse(fs.readFileSync(STORE, "utf8"));
  } catch {
    const seeded = {};
    for (const code in SEED)
      seeded[code] = SEED[code].map(label => ({ id: crypto.randomUUID(), label, votes: 1 }));
    persist(seeded);
    return seeded;
  }
}
function persist(data){
  const tmp = STORE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  fs.renameSync(tmp, STORE);            // atomic replace
}

/* ---- helpers ---- */
function send(res, code, obj){
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  });
  res.end(body);
}
function readBody(req){
  return new Promise((resolve, reject) => {
    let data = "", size = 0;
    req.on("data", c => {
      size += c.length;
      if (size > MAX_BODY_BYTES) { reject(new Error("body too large")); req.destroy(); return; }
      data += c;
    });
    req.on("end", () => { try { resolve(data ? JSON.parse(data) : {}); } catch { reject(new Error("bad json")); } });
    req.on("error", reject);
  });
}
function cleanLabel(s){
  if (typeof s !== "string") return null;
  const t = s.replace(/[\u0000-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim();
  if (!t || t.length > MAX_LABEL_LEN) return null;
  return t;
}

/* ---- API handlers ---- */
async function handleAddTag(req, res){
  const body = await readBody(req);
  const code = body.code;
  const label = cleanLabel(body.label);
  if (!CODE_RE.test(code || "") || !label) return send(res, 400, { error: "invalid code or label" });
  const list = store[code] || (store[code] = []);
  if (list.length >= MAX_TAGS_PER_CO) return send(res, 409, { error: "tag limit reached" });
  if (list.some(t => t.label.toLowerCase() === label.toLowerCase()))
    return send(res, 200, { code, tags: list });          // idempotent: already exists
  const tag = { id: crypto.randomUUID(), label, votes: 1 };
  list.push(tag);
  persist(store);
  send(res, 201, { code, tags: list, added: tag.id });
}
async function handleVote(req, res){
  const body = await readBody(req);
  const { code, id } = body;
  const dir = body.dir === -1 ? -1 : 1;
  if (!CODE_RE.test(code || "") || !store[code]) return send(res, 400, { error: "unknown coalition" });
  const tag = store[code].find(t => t.id === id);
  if (!tag) return send(res, 404, { error: "unknown tag" });
  tag.votes = Math.max(0, tag.votes + dir);
  persist(store);
  send(res, 200, { code, tags: store[code] });
}

/* ---- static ---- */
function serveHtml(res){
  fs.readFile(HTML_FILE, (err, buf) => {
    if (err) { res.writeHead(500); res.end("index.html not found next to server.js"); return; }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(buf);
  });
}

const DATA_TYPES = { ".csv": "text/csv; charset=utf-8", ".json": "application/json" };
function serveData(res, urlPath){
  // /data/<file> only — no traversal, extension-allowlisted
  const name = path.basename(urlPath);                       // strips any ../
  const ext = path.extname(name).toLowerCase();
  if (!DATA_TYPES[ext]) { res.writeHead(404); res.end(); return; }
  fs.readFile(path.join(ROOT, "data", name), (err, buf) => {
    if (err) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "Content-Type": DATA_TYPES[ext] });
    res.end(buf);
  });
}

/* ---- router ---- */
const server = http.createServer(async (req, res) => {
  try {
    const url = req.url.split("?")[0];
    if (req.method === "OPTIONS")                         return send(res, 204, {});
    if (req.method === "GET"  && (url === "/" || url === "/index.html")) return serveHtml(res);
    if (req.method === "GET"  && url.startsWith("/data/")) return serveData(res, url);
    if (req.method === "GET"  && url === "/api/tags")     return send(res, 200, store);
    if (req.method === "POST" && url === "/api/tags")     return handleAddTag(req, res);
    if (req.method === "POST" && url === "/api/vote")      return handleVote(req, res);
    send(res, 404, { error: "not found" });
  } catch (e) {
    send(res, 400, { error: e.message || "bad request" });
  }
});

server.listen(PORT, () => {
  console.log(`Johor DUN map running →  http://localhost:${PORT}`);
  console.log(`Labels persisted to     ${STORE}`);
});
