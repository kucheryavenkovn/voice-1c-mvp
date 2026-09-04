#!/usr/bin/env bash
# Установка voice-1c-mvp offline-бандла на Ubuntu/Debian (работает и на других Linux).
# Находится в каталоге бандла рядом с images.tar, docker-compose.yml, .env.example.
#
# Использование:
#   ./install.sh                          # интерактивно: спросит LLM/1С URL и модель
#   sudo ./install.sh --install-docker    # плюс установка Docker с get.docker.com
#   ./install.sh --yes                    # без вопросов, дефолты из .env.example
#   ./install.sh --lm-url http://192.168.1.50:1234/v1 --onec-url http://192.168.1.60:6003/api
#
# Флаги:
#   --install-docker   установить Docker, если не найден (нужен root/sudo)
#   --lm-url URL       адрес LLM (OpenAI-совместимый), например http://192.168.1.50:1234/v1
#   --onec-url URL     адрес 1С MCP Toolkit, например http://192.168.1.60:6003/api
#   --model ИМЯ        whisper-модель на CPU: tiny|base|small|medium|large-v3 (по умолчанию small)
#   --yes, -y          не задавать вопросов
set -euo pipefail

cd "$(dirname "$0")"

install_docker=0
lm_url=""
onec_url=""
model="small"
assume_yes=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-docker) install_docker=1 ;;
        --lm-url) lm_url="${2-}"; shift ;;
        --onec-url) onec_url="${2-}"; shift ;;
        --model) model="${2-}"; shift ;;
        --yes|-y) assume_yes=1 ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Неизвестный параметр: $1 (см. --help)" >&2; exit 1 ;;
    esac
    shift
done

say() { printf '\033[36m==> %s\033[0m\n' "$1"; }

# --- Docker ---
if ! command -v docker >/dev/null 2>&1; then
    if [[ $install_docker -eq 1 ]]; then
        say "Установка Docker (get.docker.com)"
        if [[ $EUID -eq 0 ]]; then
            curl -fsSL https://get.docker.com | sh
            systemctl enable --now docker
        else
            curl -fsSL https://get.docker.com | sudo sh
            sudo systemctl enable --now docker
        fi
    else
        echo "docker не найден." >&2
        echo "  Установи: sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin" >&2
        echo "  или запусти: sudo ./install.sh --install-docker" >&2
        exit 1
    fi
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 не найден (нужна команда 'docker compose')." >&2
    echo "  Установи: sudo apt-get install -y docker-compose-plugin" >&2
    exit 1
fi

if [[ $EUID -ne 0 ]] && ! docker info >/dev/null 2>&1; then
    echo "Нет доступа к Docker. Запусти через sudo или добавь пользователя в группу:" >&2
    echo "  sudo usermod -aG docker \$USER   # затем перелогиниться" >&2
    exit 1
fi

# --- Загрузка образов ---
say "Загрузка образов в Docker"
if [[ -f images.tar ]]; then
    docker load -i images.tar
elif compgen -G 'images.tar.part*' >/dev/null; then
    cat $(ls images.tar.part* | sort) | docker load
else
    echo "images.tar (или images.tar.part*) не найден в $(pwd)." >&2
    exit 1
fi

# --- .env ---
say "Конфигурация .env"
if [[ ! -f .env ]]; then
    cp .env.example .env
fi

get_env() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
set_env() { sed -i "s|^$1=.*|$1=$2|" .env; }

if [[ $assume_yes -eq 0 ]]; then
    if [[ -z "$lm_url" ]]; then
        default_lm="$(get_env LM_BASE_URL)"
        read -r -p "LLM (LM Studio/vLLM на другой машине, напр. http://192.168.1.50:1234/v1) [$default_lm]: " ans
        lm_url="${ans:-$default_lm}"
    fi
    if [[ -z "$onec_url" ]]; then
        default_onec="$(get_env ONEC_BASE_URL)"
        read -r -p "1С MCP Toolkit (напр. http://192.168.1.60:6003/api) [$default_onec]: " ans
        onec_url="${ans:-$default_onec}"
    fi
    read -r -p "Whisper-модель на CPU: tiny|base|small|medium|large-v3 [$model]: " ans
    model="${ans:-$model}"
fi

if [[ -n "$lm_url" ]]; then set_env LM_BASE_URL "$lm_url"; fi
if [[ -n "$onec_url" ]]; then set_env ONEC_BASE_URL "$onec_url"; fi
set_env WHISPER_MODEL "$model"
set_env WHISPER_DEVICE cpu
set_env WHISPER_COMPUTE_TYPE int8

if [[ -z "$(get_env LM_BASE_URL)" ]]; then
    echo "ВНИМАНИЕ: LM_BASE_URL пуст — LLM недоступна. Отредактируй .env и перезапусти." >&2
fi

# --- Запуск ---
say "docker compose up -d"
docker compose up -d

say "Ожидание шлюза"
port="$(get_env PORT_GATEWAY)"
port="${port:-8103}"
for _ in $(seq 1 30); do
    if curl -fsS "http://localhost:${port}/health" >/dev/null 2>&1; then
        echo ""
        echo "Готово: http://localhost:${port}"
        docker compose ps
        exit 0
    fi
    sleep 2
done

echo "Шлюз не ответил за 60 с. Смотри логи: docker compose logs voice-gateway" >&2
exit 1
