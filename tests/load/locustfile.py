"""Сценарии нагрузочного тестирования веб-панели и API (Locust).

Запуск в headless-режиме (пример):
    locust -f tests/load/locustfile.py --headless -u 50 -r 10 -t 1m --host http://localhost:8000
Интерактивный запуск через Web UI:
    locust -f tests/load/locustfile.py --host http://localhost:8000
    (затем открыть http://localhost:8089)
"""

import random

from locust import HttpUser, between, task


class StudentWidgetUser(HttpUser):
    """Имитирует активность студентов через виджет/фрейм ВК и публичные API v1."""

    wait_time = between(0.5, 2.5)

    @task(5)
    def view_frame_widget(self):
        """Загрузка главного фрейма отделов."""
        self.client.get("/departments/frame", name="/departments/frame [Widget HTML]")

    @task(4)
    def fetch_departments_api(self):
        """Получение списка отделов через JSON API v1."""
        self.client.get("/api/v1/departments", name="/api/v1/departments [JSON]")

    @task(3)
    def fetch_faq_and_search(self):
        """Запрос часто задаваемых вопросов и поиск по ключевым словам."""
        queries = ["стипендия", "общежитие", "деканат", "сессия", "справка"]
        q = random.choice(queries)
        self.client.get(f"/api/v1/faq?query={q}", name="/api/v1/faq?query=... [Search]")

    @task(2)
    def check_health(self):
        """Проверка доступности healthcheck."""
        self.client.get("/health", name="/health")

    @task(1)
    def create_ticket_simulation(self):
        """Создание тестовой заявки через публичный API."""
        payload = {
            "vk_user_id": random.randint(10000000, 99999999),
            "user_name": f"Test Student {random.randint(1, 1000)}",
            "department_id": 1,
            "topic": "Вопрос по нагрузочному тестированию",
            "text": "Автоматически сгенерированный вопрос для проверки устойчивости API и БД под нагрузкой.",
        }
        headers = {"Content-Type": "application/json"}
        with self.client.post(
            "/api/v1/tickets",
            json=payload,
            headers=headers,
            name="/api/v1/tickets [Create Ticket]",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201, 400, 422):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")


class MonitoringAndAdminUser(HttpUser):
    """Имитирует скрапинг Prometheus и служебные запросы панели."""

    wait_time = between(2, 5)

    @task(5)
    def scrape_metrics(self):
        """Сбор метрик Prometheus."""
        self.client.get("/metrics", name="/metrics [Prometheus Scrape]")

    @task(3)
    def check_health(self):
        """Проверка healthcheck."""
        self.client.get("/health", name="/health [Service Health]")

    @task(2)
    def view_login_page(self):
        """Открытие страницы входа."""
        self.client.get("/", name="/ [Login Page HTML]")


class PeakLoadBurstUser(HttpUser):
    """Имитирует резкий всплеск (spike/burst) запросов от сотен студентов одновременно.

    Используется для стресс-тестирования пула подключений PgBouncer,
    кэширования Redis, времени ответа FastAPI/Go API и устойчивости к 429/503.
    """

    # Минимальная задержка между запросами для создания пикового стресса
    wait_time = between(0.05, 0.25)

    @task(6)
    def fast_faq_search(self):
        """Параллельный поиск по FAQ (проверка Redis кэша и FTS в БД)."""
        queries = ["стипендия", "деканат", "сессия", "перевод", "задолженность", "справка", "обходной лист"]
        q = random.choice(queries)
        self.client.get(f"/api/v1/faq?query={q}", name="/api/v1/faq [Burst Search]")

    @task(4)
    def rapid_frame_load(self):
        """Мгновенное открытие виджета студентами при рассылке уведомления."""
        self.client.get("/departments/frame", name="/departments/frame [Burst Frame]")

    @task(2)
    def rapid_ticket_creation(self):
        """Массовая подача заявок при анонсах."""
        payload = {
            "vk_user_id": random.randint(10000000, 99999999),
            "user_name": f"Burst Student {random.randint(1, 10000)}",
            "department_id": random.choice([1, 2, 3]),
            "topic": "Пиковая нагрузка: подача заявки",
            "text": "Стресс-тест одновременной записи обращений в очередь Outbox и БД.",
        }
        with self.client.post(
            "/api/v1/tickets",
            json=payload,
            headers={"Content-Type": "application/json"},
            name="/api/v1/tickets [Burst Ticket Submit]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201, 400, 422, 429):
                resp.success()
            else:
                resp.failure(f"Burst stress failed with code {resp.status_code}")

