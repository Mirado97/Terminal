# Деплой Arbitrage Terminal на VPS

Бэкенд работает на VPS. Фронтенд запускается локально на Windows и подключается к VPS по IP.

---

## Требования к серверу

- Ubuntu 22.04 LTS (или 20.04)
- Python 3.10+
- 512 MB RAM минимум
- Открытые порты: **8080** (API + WebSocket)

Node.js на VPS не нужен.

---

## 1. Подключение и подготовка

```bash
ssh root@<IP_VPS>

apt update && apt upgrade -y
apt install -y python3.10 python3.10-venv python3-pip screen

```

---

## 2. Загрузка кода на VPS

Выполнить на Windows (нужен rsync через WSL или Git Bash):
```bash
rsync -avz --exclude='.venv' --exclude='frontend/node_modules' \
  /mnt/d/Antigravity/Progect/Terminal/ \
  root@<IP_VPS>:/opt/terminal/
```

Или через scp (PowerShell):
```powershell
scp -r D:\Antigravity\Progect\Terminal root@157.230.42.170:/opt/terminal
```

Папку `frontend/` можно не копировать на VPS — она там не нужна.

---

## 3. Установка зависимостей

```bash
cd /opt/terminal
python3.10 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

pip install \
  aiohttp==3.13.5 \
  asyncpg==0.31.0 \
  msgspec==0.21.1 \
  numpy==2.2.6 \
  orjson==3.11.9 \
  prometheus_client==0.25.0 \
  python-dotenv==1.2.2 \
  python-socks==2.8.1 \
  PyYAML==6.0.3 \
  redis==7.4.0 \
  structlog==25.5.0 \
  websockets==16.0
```

---

## 4. Настройка окружения

```bash
cat > /opt/terminal/.env << 'EOF'
# Для режима наблюдения (без торговли) можно оставить пустыми
POSTGRES_URL=
REDIS_URL=

BYBIT_API_KEY=
BYBIT_API_SECRET=
GATE_API_KEY=
GATE_API_SECRET=
EOF
```

---

## 5. Запуск бэкенда

### Вручную (для проверки):
```bash
cd /opt/terminal
source .venv/bin/activate
python run.py
```

### Через screen (не закрывается при отключении SSH):
```bash
screen -S terminal
cd /opt/terminal && source .venv/bin/activate && python run.py
# Ctrl+A, D — отсоединиться, бот продолжает работать

# Вернуться к логам:
screen -r terminal
```

### Через systemd (автозапуск при перезагрузке VPS):
```bash
cat > /etc/systemd/system/arbitrage.service << 'EOF'
[Unit]
Description=Arbitrage Terminal
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/terminal
ExecStart=/opt/terminal/.venv/bin/python run.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/terminal/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable arbitrage
systemctl start arbitrage

# Логи:
journalctl -u arbitrage -f
```

---

## 6. Открытие порта на VPS

```bash
ufw allow 22      # SSH
ufw allow 8080    # API + WebSocket
ufw enable
```

Проверить что бэкенд доступен:
```
http://<IP_VPS>:8080/api/status
```

---

## 7. Подключение локального фронтенда к VPS

На Windows создать файл `frontend\.env.local`:
```
NEXT_PUBLIC_API_URL=http://<IP_VPS>:8080
NEXT_PUBLIC_WS_URL=ws://<IP_VPS>:8080/ws
```

Затем запустить фронтенд локально как обычно:
```powershell
cd D:\Antigravity\Progect\Terminal\frontend
npm run dev
```

Открыть в браузере: `http://localhost:3000` — данные будут идти с VPS.

---

## Частые проблемы

| Ошибка | Решение |
|--------|---------|
| `ModuleNotFoundError` | Забыли `source .venv/bin/activate` |
| `[Errno 98] bind error` | Порт 8080 занят — `fuser -k 8080/tcp` |
| Фронтенд не видит данные | Проверить `frontend/.env.local`, перезапустить `npm run dev` |
| `Не удалось загрузить пары` | API биржи недоступен — бот продолжит с fallback-списком |
| MEXC WS не подключается | Нужен SOCKS5-прокси (адаптер закомментирован в `run.py`) |
