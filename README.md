# AI-Travel Agent: Умный конструктор путешествий

MVP веб-сервис, который превращает нечеткий запрос пользователя в реалистичный маршрут по дням и часам. Источники идей — Wikivoyage через RAG, фактические точки — только из Yandex Search API, логистика — через Yandex Routing API. На фронтенде карта не отображается: пользователь видит таймлайн, адреса, время в пути, оценку бюджета и ссылки на Яндекс.Карты.

## Что умеет MVP
- принимает `query`, `days`, `budget`, `mode`
- извлекает город и интересы из текста запроса
- подтягивает подсказки из Wikivoyage
- генерирует поисковые фразы для Yandex Search API
- строит маршрут по дням с учётом окна `10:00–20:00`
- отбрасывает точки с переездом больше 45 минут
- считает примерный бюджет
- генерирует ссылки на Яндекс.Карты для каждой точки и для маршрута дня

## Assumptions
- Для локальной проверки без рабочих ключей Яндекса есть `MOCK_MODE=true`. В этом режиме backend использует детерминированные мок-данные, но API-контракт остаётся тем же.
- В боевом сценарии `MOCK_MODE=false`, а итоговые точки берутся только из Yandex Search API.
- Из Wikivoyage используется MediaWiki API с текстом страницы города; если страница недоступна, RAG мягко деградирует и всё равно строит план через эвристику.
- `mode` маппится в Yandex Routing API как `walking|transit|driving`, а в ссылки Яндекс.Карт как `pd|mt|auto`.

## Структура
```text
ai-travel-agent/
  README.md
  .env.example
  backend/
  frontend/
```

## Переменные окружения
Корневой `.env` не обязателен, используются `.env` внутри `backend/` и `frontend/`.

### Backend
Скопируйте `backend/.env.example` в `backend/.env` и заполните:
- `YANDEX_PLACES_API_KEY`
- `YANDEX_ROUTING_API_KEY`
- `OPENAI_API_KEY` — опционально
- `OPENAI_MODEL` — опционально
- `LLM_ENABLED=true|false`
- `MOCK_MODE=true|false`

### Frontend
Скопируйте `frontend/.env.example` в `frontend/.env`:
- `VITE_API_BASE_URL=http://127.0.0.1:8000`

## Быстрый старт

### Windows (PowerShell)
```powershell
cd ai-travel-agent\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# отредактируйте .env при необходимости
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Во втором окне:
```powershell
cd ai-travel-agent\frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Smoke test:
```powershell
cd ai-travel-agent\backend
.\.venv\Scripts\Activate.ps1
python scripts\smoke_test.py
```

### macOS (zsh)
```zsh
cd ai-travel-agent/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Во втором терминале:
```zsh
cd ai-travel-agent/frontend
npm install
cp .env.example .env
npm run dev
```

Smoke test:
```zsh
cd ai-travel-agent/backend
source .venv/bin/activate
python scripts/smoke_test.py
```

### Linux (bash)
```bash
cd ai-travel-agent/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Во втором терминале:
```bash
cd ai-travel-agent/frontend
npm install
cp .env.example .env
npm run dev
```

Smoke test:
```bash
cd ai-travel-agent/backend
source .venv/bin/activate
python scripts/smoke_test.py
```

## Частые проблемы
### CORS
Если фронтенд не достучался до backend, проверьте `BACKEND_CORS_ORIGINS` в `backend/.env` и `VITE_API_BASE_URL` во `frontend/.env`.

### Перепутали lat/lon
Yandex Search API возвращает координаты в формате `[lon, lat]`. В проекте они нормализуются в `{lat, lon}`. Для Routing API waypoints собираются как `lat,lon`.

### Ключ Яндекса “не работает первые 10–15 минут”
После создания ключа иногда нужно немного подождать, пока доступ распространится. Это нормальная задержка со стороны провайдера API. citeturn0search0turn0search4

### 429 rate limit
Для Search и Routing есть retry и TTL-кеш. Если 429 повторяется, уменьшите частоту запросов и подождите.

## Как сделать демо
### Демо-запрос 1
```json
{
  "query": "Хочу выходные в Санкт-Петербурге, люблю рок-бары и современное искусство",
  "days": 2,
  "budget": 15000,
  "mode": "walking"
}
```

### Демо-запрос 2
```json
{
  "query": "Один день в Казани, люблю национальную кухню, исторический центр и спокойные прогулки",
  "days": 1,
  "budget": 7000,
  "mode": "transit"
}
```

## Что показать на защите
1. Форму ввода: расплывчатый запрос + дни + бюджет + режим.
2. JSON-ответ backend и соответствие контракту.
3. Таймлайн на фронтенде без карты.
4. Ссылки “Маршрут сюда” и “Открыть маршрут дня”.
5. Гео-валидацию: показать, что дальняя точка отбрасывается при `>45` минут.
6. Budget summary и пометку, что это оценка.
7. RAG snippets из Wikivoyage и generated search phrases.
8. Режимы `walking / transit / driving`.
9. `MOCK_MODE=true` для быстрой демонстрации и `MOCK_MODE=false` для реальных API.
10. Smoke test и успешную сборку frontend.
