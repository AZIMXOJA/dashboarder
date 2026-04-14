# Smartup Sales Dashboard

Telegram Web App + браузерный дашборд для руководства дистрибьюции.

## Структура

```
smartup-dashboard/
├── backend/
│   ├── main.py           # FastAPI — все API endpoints
│   └── requirements.txt
├── frontend/
│   └── index.html        # Single-page dashboard (TG WebApp + браузер)
├── render.yaml
└── README.md
```

## Деплой на Render

### 1. Залить на GitHub
```bash
git init
git add .
git commit -m "initial"
git remote add origin https://github.com/YOUR/smartup-dashboard.git
git push -u origin main
```

### 2. Создать Web Service на Render
- **Runtime:** Python 3
- **Build Command:** `pip install -r backend/requirements.txt`
- **Start Command:** `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

### 3. Env Variables на Render

| Variable | Value | Description |
|----------|-------|-------------|
| `SHEET_ID` | `1BxiMVs0...` | ID Google Sheets (из URL) |
| `USD_RATE` | `12700` | Курс USD→UZS (обновлять вручную) |
| `API_URL` | `https://your-app.onrender.com` | URL самого сервиса (для frontend) |
| `SHEET_GIDS` | см. ниже | GID каждого листа |

### 4. Получить SHEET_GIDS

Открой каждый лист в Google Sheets, в URL найди `gid=XXXXXXXX`.
Формат для env variable:
```
Orders:123456,Returns:234567,KpiPlans:345678,...
```

Если GIDs не указаны — используются дефолтные (0,1,2...) которые совпадают с порядком создания листов через Apps Script.

### 5. Сделать Google Sheets публичным
Файл → Поделиться → Все у кого есть ссылка → Просматривающий

### 6. Подключить к Telegram боту
В BotFather создай кнопку Menu Button:
```
/setmenubutton
URL: https://your-app.onrender.com
```

Или используй как inline WebApp кнопку в боте.

## Для нового клиента

1. Создай новый Google Sheets для клиента
2. Подключи тот же `Code.gs` (Apps Script)
3. На Render — создай новый Web Service или добавь env `SHEET_ID_2`
4. Всё остальное одинаково

## API Endpoints

| Endpoint | Params | Description |
|----------|--------|-------------|
| `GET /` | — | Frontend (index.html) |
| `GET /health` | — | Health check |
| `GET /api/meta` | — | Филиалы + курс USD |
| `GET /api/summary` | filial_id, period, currency | Сводка |
| `GET /api/chart/revenue_by_day` | filial_id, period, currency | График по дням |
| `GET /api/chart/revenue_by_filial` | period, currency | По филиалам |
| `GET /api/agents` | filial_id, period, currency | Агенты |
| `GET /api/products` | filial_id, period, currency, group_by, limit | Товары |
| `GET /api/kpi` | filial_id, currency | План/Факт |
| `GET /api/clients` | filial_id, currency | Клиенты |

## Период (`period`)
- `today` — сегодня
- `week` — последние 7 дней  
- `month` — текущий месяц (default)

## Валюта (`currency`)
- `UZS` — узбекский сум (default)
- `USD` — доллар (конвертация по `USD_RATE`)
