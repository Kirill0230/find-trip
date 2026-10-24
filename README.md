# О!дин запрос — прототип AI-агента для путешествий

> Скажи, куда хочешь отправиться. Остальное устроит агент.

Одно сообщение → лента роликов отелей с озвучкой → свайпы вместо анкеты → подборка с объяснениями → пакет «отель + билеты + трансфер» с проверкой состыковок → доуточнение → имитация оплаты, план по часам и ваучеры.

- Требования: [docs/TRACK.md](docs/TRACK.md) (кейс), [docs/TZ.md](docs/TZ.md) (ТЗ на MVP, оригинал — PDF рядом)
- План и статус по ролям: [docs/PLAN.md](docs/PLAN.md)
- Сценарий защиты и честный список моков: [docs/DEMO.md](docs/DEMO.md)

## Запуск

```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../frontend && npm install && npm run build
cd .. && backend/.venv/bin/uvicorn app.main:app --app-dir backend --port 8000
```

Открыть http://localhost:8000 (в режиме телефона в DevTools). Для разработки фронта: `npm run dev` в `frontend/` (порт 5173, API проксируется на 8000).

Docker: `docker build -t odin . && docker run -p 8000:8000 odin`.

Переменные окружения (все необязательны):

| Переменная | Что делает |
|---|---|
| `OFFLINE=1` | не ходить в сеть (погода из климатических норм) |
| `ANTHROPIC_API_KEY` (+ `pip install anthropic`) | LLM дополняет разбор запроса; без ключа — правила |
| `TRAVELPAYOUTS_TOKEN` | калибровать цены авиабилетов по кэшу Aviasales |
| `DATABASE_URL` | по умолчанию SQLite `backend/odin.db`; можно PostgreSQL |
| `AGENT_TODAY` | зафиксировать «сегодня» для воспроизводимых тестов |

## Тесты и метрики

```bash
cd backend
OFFLINE=1 .venv/bin/pytest -q
OFFLINE=1 .venv/bin/python -m tests.run_metrics
```

Прогон 20 тестовых запросов ([backend/tests/test_queries.json](backend/tests/test_queries.json)), отчёт — `backend/tests/metrics_report.json`, сводка — `GET /metrics`:

| Метрика | Цель ТЗ | Результат |
|---|---|---|
| Доля сценариев до оплаты | 20 из 20 | 20 из 20 |
| Время до первого ролика | < 10 с | ≤ 20 мс без сети (с Open-Meteo — до ~2,5 с на первый запрос направления) |
| Вопросов к пользователю | ≤ 3 | 0,15 в среднем, максимум 1 |
| Свайпов до подборки | 10–15 | 3,2 (подборка каждые 3 лайка; в симуляции лайки частые) |
| Точность разбора | ≥ 90% | 100% на своих тестах — правила подгонялись под них; на незнакомых запросах ниже, см. DEMO.md |
| Ошибки в состыковках до оплаты | 0 | 0 (оплата блокируется при любой ошибке) |
| Пересборка не с нуля | все тесты с правкой | 9 из 9 |

## Архитектура

```
frontend (React + Vite, 6 экранов под 390–430 px)
   │ REST + SSE
backend (FastAPI)
   ├── agent/
   │   ├── graph.py        мини-StateGraph с API как у LangGraph; граф повторяет BPMN, трасса узлов → UI «Как думал агент»
   │   ├── flows.py        графы intake / research / correction, операции API
   │   ├── parser.py       parse_request без LLM: даты, бюджет, гости, откуда/куда, отель/номер, ограничения, настроение
   │   ├── llm.py          опциональная LLM (structured output), дополняет правила с таймаутом
   │   ├── ranking.py      фильтры + score = Σ w·f − 0.3·sim(skipped) + 0.1·novelty, правила свайпов
   │   ├── explain.py      voiceover (≤ 50 слов) и why / tradeoffs / risks из данных
   │   ├── package.py      сборка и пересборка пакета по компонентам
   │   └── corrections.py  фраза → дельта профиля → затронутые компоненты
   ├── tools/              search_hotels, get_hotel_price, search_flights, search_trains,
   │                       search_transfer, get_weather, check_connections, book (каждый вызов журналируется)
   ├── data/               8 направлений, 40 отелей (синтетика)
   └── store.py            SQLite/SQLAlchemy: состояние по session_id + события
```

LLM берёт на себя только язык (и то опционально), а фильтры, ранжирование, проверку состыковок и оплату делает обычный код — поэтому незнакомый запрос не роняет сценарий и доходит до ленты за миллисекунды.

**Пересборка не с нуля.** Правка превращается в дельту (`AFFECTS` в `corrections.py`): «на 2 дня позже» → пересчитать цену отеля, билеты, трансфер, отель и номер оставить; «рейс быстрее» → выбрать из уже найденных рейсов без нового поиска; «с собакой» → проверить текущий отель и заменить, только если он не пускает животных. В ответе есть `recomputed` / `kept` / `tool_calls` — это видно в UI и проверяется в метриках.

## API (договор из ТЗ)

| Метод | Что делает |
|---|---|
| `POST /session` | текст → профиль, чипы «что понял», направления, `mode`: feed / checkout / clarify |
| `GET /session/{id}/feed?n=3` | следующие ролики с текстом озвучки и ценой |
| `POST /session/{id}/swipe` | `video_id`, `direction` (right / left / watched) → веса, флаг «показать подборку» |
| `GET /session/{id}/shortlist` | подборка с why / tradeoffs / risks (SSE, `?stream=0` — JSON) |
| `POST /session/{id}/correct` | текст правки → новая лента / подборка / пакет |
| `POST /session/{id}/package` | выбранный отель → пакет (SSE со статусами шагов) |
| `PATCH /session/{id}/package` | правки со страницы доуточнения + «Что-нибудь ещё?» |
| `POST /session/{id}/book` | данные гостей → заказ, план, ваучеры; 409 при конфликте состыковок |
| `GET /metrics`, `GET /graph` | метрики; граф агента в Mermaid |
