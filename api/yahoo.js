// Proxy tunggal ke Yahoo Finance (dipakai fallback per-saham). Hanya host chart Yahoo yang diizinkan.
const OK = /^https:\/\/query[12]\.finance\.yahoo\.com\/v8\/finance\/chart\//;
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36";
module.exports = async (req, res) => {
  const u = req.query.url;
  if (!u || !OK.test(u)) return res.status(400).json({ error: "url tidak diizinkan" });
  try {
    const r = await fetch(u, { headers: { "User-Agent": UA, Accept: "application/json" } });
    const body = await r.text();
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", /range=5d/.test(u) ? "public, s-maxage=15, stale-while-revalidate=60" : "public, s-maxage=3600, stale-while-revalidate=86400");
    res.status(r.status).send(body);
  } catch (e) { res.status(502).json({ error: String(e) }); }
};
