/* ====================================================================
   Rota · сервер онлайн-регистрации
   --------------------------------------------------------------------
   Крошечный REST API для саморегистрации игроков на турнир.
   Организатор создаёт сессию → получает КОД и ссылку → делится в Telegram.
   Игроки открывают ссылку, вводят имя → попадают в список.
   Хранилище — обычный JSON-файл (без нативных зависимостей). Для
   постоянного хранения на Railway смонтируй volume в каталог DATA_DIR
   (по умолчанию ./data).
   ==================================================================== */

import express from 'express';
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';

const PORT = process.env.PORT || 3000;
const DATA_DIR = process.env.DATA_DIR || './data';
const MAX_NAME_LEN = 32;
const HARD_CAP = 40;               // абсолютный потолок игроков в одной сессии
const TTL_MS = 1000 * 60 * 60 * 24 * 3; // сессии живут 3 дня, потом чистятся
const INSTANCE_ID = randomBytes(4).toString('hex'); // для диагностики реплик

if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
const DB_PATH = join(DATA_DIR, 'rota.json');

/* ---------- хранилище: JSON-файл на общем volume — источник истины ----------
   Сервис может работать в нескольких репликах на общем volume. Чтобы реплики
   не расходились, НЕ кэшируем состояние в памяти между запросами: читаем файл
   заново на каждом обращении (файл крошечный) и пишем синхронно и атомарно
   (tmp + rename). Так любой инстанс всегда видит последнее записанное состояние
   — независимо от разрешения mtime на файловой системе. */
let db = { tournaments: {} };

function loadDb() {
  try {
    db = existsSync(DB_PATH) ? JSON.parse(readFileSync(DB_PATH, 'utf8')) : { tournaments: {} };
  } catch (e) { console.warn('Не удалось прочитать БД:', e.message); db = { tournaments: {} }; }
}

function persist() {
  try {
    const tmp = DB_PATH + '.' + INSTANCE_ID + '.tmp';
    writeFileSync(tmp, JSON.stringify(db));
    renameSync(tmp, DB_PATH);
  } catch (e) { console.warn('Ошибка записи БД:', e.message); }
}

// Прочитать свежее состояние файла перед каждым обращением к db.
function fresh() { loadDb(); return db; }

function sweepExpired() {
  loadDb();
  const now = Date.now();
  let changed = false;
  for (const code of Object.keys(db.tournaments)) {
    if (now - db.tournaments[code].createdAt > TTL_MS) { delete db.tournaments[code]; changed = true; }
  }
  if (changed) persist();
}
setInterval(sweepExpired, 1000 * 60 * 60).unref();

/* ---------- helpers ---------- */
// Код без легко путаемых символов (0/O, 1/I) — удобно диктовать/набирать.
const CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
function randStr(len) {
  let s = '';
  for (let i = 0; i < len; i++) s += CODE_ALPHABET[Math.floor(Math.random() * CODE_ALPHABET.length)];
  return s;
}
function cleanName(raw) {
  return String(raw == null ? '' : raw).replace(/\s+/g, ' ').trim().slice(0, MAX_NAME_LEN);
}
function publicView(t) {
  if (!t) return null;
  return {
    code: t.code,
    title: t.title,
    maxPlayers: t.maxPlayers,
    closed: !!t.closed,
    players: t.players.map(p => ({ id: p.id, name: p.name })),
  };
}

/* ---------- app ---------- */
const app = express();
app.use(express.json({ limit: '16kb' }));

// CORS — фронтенд живёт на другом origin (GitHub Pages).
app.use((req, res, next) => {
  res.set('Access-Control-Allow-Origin', '*');
  res.set('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.set('Access-Control-Allow-Headers', 'Content-Type, X-Admin-Token');
  // Ответы динамические — запрещаем кэширование, иначе опрос организатора
  // будет получать устаревший (закэшированный браузером) список игроков.
  res.set('Cache-Control', 'no-store');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

app.get('/', (_req, res) => res.json({ ok: true, service: 'rota-registration' }));
app.get('/health', (_req, res) => res.json({ ok: true, instance: INSTANCE_ID }));

// Создать сессию регистрации.
app.post('/api/tournaments', (req, res) => {
  const title = cleanName(req.body?.title || '').slice(0, 64);
  let maxPlayers = parseInt(req.body?.maxPlayers, 10);
  if (!Number.isFinite(maxPlayers)) maxPlayers = 8;
  maxPlayers = Math.max(4, Math.min(HARD_CAP, maxPlayers));

  const store = fresh();
  let code;
  for (let i = 0; i < 12; i++) { const c = randStr(5); if (!store.tournaments[c]) { code = c; break; } }
  if (!code) return res.status(500).json({ error: 'code_collision' });

  const adminToken = randStr(24);
  store.tournaments[code] = { code, title, maxPlayers, adminToken, closed: false, createdAt: Date.now(), seq: 0, players: [] };
  persist();
  res.status(201).json({ code, adminToken, maxPlayers, title });
});

// Публичное состояние сессии (для организатора и игроков).
app.get('/api/tournaments/:code', (req, res) => {
  const t = fresh().tournaments[req.params.code.toUpperCase()];
  if (!t) return res.status(404).json({ error: 'not_found' });
  res.json(publicView(t));
});

// Саморегистрация игрока.
app.post('/api/tournaments/:code/register', (req, res) => {
  const t = fresh().tournaments[req.params.code.toUpperCase()];
  if (!t) return res.status(404).json({ error: 'not_found' });
  if (t.closed) return res.status(409).json({ error: 'closed' });

  const name = cleanName(req.body?.name);
  if (!name) return res.status(400).json({ error: 'empty_name' });
  if (t.players.length >= t.maxPlayers) return res.status(409).json({ error: 'full' });
  if (t.players.some(p => p.name.toLowerCase() === name.toLowerCase()))
    return res.status(409).json({ error: 'duplicate' });

  const player = { id: ++t.seq, name };
  t.players.push(player);
  persist();
  res.status(201).json({ id: player.id, name, tournament: publicView(t) });
});

/* ---------- admin (требуют X-Admin-Token) ---------- */
function requireAdmin(req, res) {
  const t = fresh().tournaments[req.params.code.toUpperCase()];
  if (!t) { res.status(404).json({ error: 'not_found' }); return null; }
  const token = req.get('X-Admin-Token');
  if (!token || token !== t.adminToken) { res.status(403).json({ error: 'forbidden' }); return null; }
  return t;
}

// Удалить игрока (организатор).
app.delete('/api/tournaments/:code/players/:id', (req, res) => {
  const t = requireAdmin(req, res);
  if (!t) return;
  const id = parseInt(req.params.id, 10);
  t.players = t.players.filter(p => p.id !== id);
  persist();
  res.json(publicView(t));
});

// Открыть/закрыть регистрацию (организатор).
app.post('/api/tournaments/:code/close', (req, res) => {
  const t = requireAdmin(req, res);
  if (!t) return;
  t.closed = req.body?.closed === false ? false : true;
  persist();
  res.json(publicView(t));
});

app.listen(PORT, () => console.log(`Rota registration server on :${PORT}`));
