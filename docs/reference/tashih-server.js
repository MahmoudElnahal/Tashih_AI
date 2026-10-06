// Tashih - Hadith search server
// Data: fawazahmed0/hadith-api (open source) served from the jsDelivr CDN.
// Includes the six books with grades (Al-Albani, Zubair Ali Zai...). Bukhari & Muslim carry no
// grades in the data because their hadith are authentic by the books' own condition.
//
// Requirements: Node 18+ (no npm install)
// Run:   node tashih-server.js
// Test:  http://localhost:3000/search?q=إنما الأعمال بالنيات
// Deploy (Render free): New Web Service -> Build: (empty) -> Start: node tashih-server.js

const http = require('http');

const PORT = process.env.PORT || 3000;
const BASE = 'https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1/editions/';

const BOOKS = [
  { id: 'ara-bukhari', ar: 'صحيح البخاري', sahihBook: true },
  { id: 'ara-muslim', ar: 'صحيح مسلم', sahihBook: true },
  { id: 'ara-abudawud', ar: 'سنن أبي داود' },
  { id: 'ara-tirmidhi', ar: 'جامع الترمذي' },
  { id: 'ara-nasai', ar: 'سنن النسائي' },
  { id: 'ara-ibnmajah', ar: 'سنن ابن ماجه' }
];

function normalize(s) {
  return String(s || '')
    .replace(/[ً-ٰٟـ]/g, '')
    .replace(/[أإآٱ]/g, 'ا')
    .replace(/ى/g, 'ي')
    .replace(/ة/g, 'ه')
    .replace(/[^ء-غف-ي\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function classify(grades, book) {
  if (!grades || !grades.length) {
    return book.sahihBook
      ? { code: 'sahih', ar: 'صحيح', by: 'أخرجه ' + book.ar.replace('صحيح ', 'الإمام ') + ' في صحيحه' }
      : { code: 'unclear', ar: 'لا يوجد حكم في قاعدة البيانات', by: '' };
  }
  const g = grades.find(x => /albani/i.test(x.name)) || grades[0];
  const t = g.grade.toLowerCase();
  let code = 'unclear', ar = g.grade;
  if (/maudu|mawdu|fabricated|forged/.test(t)) { code = 'mawdu'; ar = 'موضوع'; }
  else if (/daif|munkar|shadh|weak/.test(t)) { code = 'daif'; ar = /very/.test(t) ? 'ضعيف جدا' : /munkar/.test(t) ? 'منكر' : /shadh/.test(t) ? 'شاذ' : 'ضعيف'; }
  else if (/hasan/.test(t) && !/sahih/.test(t)) { code = 'sahih'; ar = 'حسن'; }
  else if (/sahih/.test(t)) { code = 'sahih'; ar = /hasan/.test(t) ? 'حسن صحيح' : 'صحيح'; }
  return { code, ar, by: g.name };
}

let INDEX = [];
let READY = false;
let LOAD_ERROR = null;

async function load() {
  const all = [];
  for (const b of BOOKS) {
    const r = await fetch(BASE + b.id + '.min.json', { signal: AbortSignal.timeout(120000) });
    if (!r.ok) throw new Error(b.id + ' -> ' + r.status);
    const data = await r.json();
    for (const h of data.hadiths) {
      if (!h.text) continue;
      const norm = normalize(h.text);
      if (norm.length < 10) continue;
      all.push({ book: b, num: h.hadithnumber, text: h.text, norm, grades: h.grades || [] });
    }
    console.log('loaded', b.id, data.hadiths.length);
  }
  INDEX = all;
  READY = true;
  console.log('index ready:', INDEX.length, 'hadiths');
}
load().catch(e => { LOAD_ERROR = String(e.message || e); console.error('LOAD FAILED', LOAD_ERROR); });

function search(q) {
  const nq = normalize(q);
  const tokens = [...new Set(nq.split(' ').filter(t => t.length > 1))];
  if (tokens.length < 2) return [];

  const out = [];
  for (const h of INDEX) {
    let score = 0;
    if (h.norm.includes(nq)) score = 1;
    else if (tokens.length >= 3) {
      const set = h._set || (h._set = new Set(h.norm.split(' ')));
      let hit = 0;
      for (const t of tokens) if (set.has(t)) hit++;
      const ratio = hit / tokens.length;
      if (ratio >= 0.85) score = Math.round(ratio * 100) / 100 * 0.95;
    }
    if (score > 0) out.push({ h, score });
  }
  const rank = h => BOOKS.indexOf(h.book);
  out.sort((a, b) => b.score - a.score || rank(a.h) - rank(b.h) || a.h.norm.length - b.h.norm.length);
  return out.slice(0, 5).map(({ h, score }) => ({
    text: h.text.replace(/[‏‎]/g, '').slice(0, 700),
    book: h.book.ar,
    number: h.num,
    grades: h.grades,
    verdict: classify(h.grades, h.book),
    score: Math.round(score * 100) / 100
  }));
}

http
  .createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Access-Control-Allow-Origin', '*');
    if (url.pathname === '/health') return res.end(JSON.stringify({ ready: READY, count: INDEX.length, error: LOAD_ERROR }));
    if (url.pathname !== '/search') { res.statusCode = 404; return res.end(JSON.stringify({ error: 'use /search?q=...' })); }
    if (!READY) { res.statusCode = 503; return res.end(JSON.stringify({ error: LOAD_ERROR || 'index is loading, retry in a minute' })); }
    const q = url.searchParams.get('q') || '';
    res.end(JSON.stringify({ query: q, matches: search(q) }));
  })
  .listen(PORT, () => console.log('Tashih server on http://localhost:' + PORT));
