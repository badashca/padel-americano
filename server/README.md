# Rota · сервер онлайн-регистрации

Крошечный бэкенд для саморегистрации игроков на турнир. Фронтенд (Rota,
`index.html`) остаётся на GitHub Pages, а этот сервер хранит списки
регистраций и отдаёт их по REST API.

## Запуск локально

```bash
cd server
npm install
npm start           # слушает http://localhost:3000
```

## Деплой на Railway

1. Зайди на https://railway.com/ → **New Project** → **Deploy from GitHub repo**
   (или **Empty Project** и подключи репозиторий).
2. Railway найдёт `server/package.json`. Если проект не в корне репозитория —
   в **Settings → Root Directory** укажи `server`.
3. Стартовая команда: `npm start` (Railway подхватит из `package.json`).
4. **(Важно, чтобы список не терялся при передеплое)**
   Settings → **Volumes** → добавь volume, mount path: `/data`.
   Затем Variables → добавь `DATA_DIR=/data`.
5. После деплоя Railway даст публичный URL вида
   `https://rota-production-xxxx.up.railway.app`.
6. Скопируй этот URL и вставь его в `index.html` в константу `API_BASE`
   (см. вверху `<script>`), затём задеплой фронтенд на GitHub Pages.

## API

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/api/tournaments` | создать сессию `{title, maxPlayers}` → `{code, adminToken, maxPlayers}` |
| `GET`  | `/api/tournaments/:code` | публичное состояние сессии + список игроков |
| `POST` | `/api/tournaments/:code/register` | регистрация `{name}` (409 при `full`/`duplicate`/`closed`) |
| `DELETE` | `/api/tournaments/:code/players/:id` | удалить игрока (заголовок `X-Admin-Token`) |
| `POST` | `/api/tournaments/:code/close` | закрыть/открыть регистрацию `{closed}` (заголовок `X-Admin-Token`) |

`adminToken` знает только организатор (хранится в его браузере) — по нему
доступны операции удаления и закрытия регистрации.
