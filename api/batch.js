// Ambil banyak saham sekaligus (paralel di server) -> 1 request dari browser, jauh lebih cepat.
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36";
const RANGES = ["5d", "1y", "2y", "3y", "5y", "10y"];
async function one(sym, range) {
  const s = sym === "^JKSE" ? sym : sym + ".JK";
  for (const host of ["query1", "query2"]) {
    try {
      const r = await fetch(`https://${host}.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}?range=${range}&interval=1d`, { headers: { "User-Agent": UA, Accept: "application/json" } });
      const d = await r.json();
      if (d && d.chart && d.chart.result) return d;
    } catch (e) { /* coba host berikutnya */ }
  }
  return { chart: { result: null, error: { description: "gagal" } } };
}
module.exports = async (req, res) => {
  const range = String(req.query.range || "5d");
  const syms = String(req.query.symbols || "").split(",").map(x => x.trim().toUpperCase()).filter(x => /^([A-Z0-9]{2,6}|\^JKSE)$/.test(x)).slice(0, 60);
  if (!RANGES.includes(range) || !syms.length) return res.status(400).json({ error: "parameter salah" });
  const out = {};
  await Promise.all(syms.map(async s => { out[s] = await one(s, range); }));
  res.setHeader("Cache-Control", range === "5d" ? "public, s-maxage=15, stale-while-revalidate=60" : "public, s-maxage=3600, stale-while-revalidate=86400");
  res.status(200).json(out);
};
