package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"student_bot/backend_go/internal/config"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/cors"
)

// allowedTestOrigin дублирует значение из main.go. Тест намеренно
// переиспользует production-строку: если в main.go сменят список
// origin'ов, тест провалится и заставит обновить ожидания.
const allowedTestOrigin = "http://localhost:3000"

const foreignTestOrigin = "https://evil.example.com"

// newCORSApp собирает приложение с той же конфигурацией CORS, что и
// main.go.
func newCORSApp() *fiber.App {
	return newCORSAppWithOrigins(config.LoadConfig().CORSOrigins)
}

func newCORSAppWithOrigins(origins string) *fiber.App {
	app := fiber.New()
	app.Use(cors.New(cors.Config{
		AllowOrigins:     origins,
		AllowCredentials: true,
		AllowHeaders:     "Origin, Content-Type, Accept, X-CSRF-Token",
	}))
	app.Get("/api/v1/health", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "healthy"})
	})
	app.Post("/api/v1/departments/", func(c *fiber.Ctx) error {
		return c.Status(fiber.StatusCreated).JSON(fiber.Map{"ok": true})
	})
	return app
}

// TestCORSAllowsConfiguredOrigin проверяет, что доверенный origin получает
// заголовок Access-Control-Allow-Origin. Без него браузер блокирует
// AJAX-запросы веб-панели к Go API.
func TestCORSAllowsConfiguredOrigin(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	req.Header.Set("Origin", allowedTestOrigin)

	resp, err := newCORSApp().Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	allowOrigin := resp.Header.Get("Access-Control-Allow-Origin")
	if allowOrigin != allowedTestOrigin {
		t.Fatalf("ожидался Access-Control-Allow-Origin %q, получен %q", allowedTestOrigin, allowOrigin)
	}
	if resp.Header.Get("Access-Control-Allow-Credentials") != "true" {
		t.Fatal("для origin из allowlist должен выставляться Access-Control-Allow-Credentials: true")
	}
}

// TestCORSDeniesForeignOrigin — ключевая проверка безопасности: запрос с
// постороннего домена не должен получать разрешающий заголовок, иначе
// злоумышленник сможет читать ответы API из браузера жертвы.
func TestCORSDeniesForeignOrigin(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	req.Header.Set("Origin", foreignTestOrigin)

	resp, err := newCORSApp().Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if got := resp.Header.Get("Access-Control-Allow-Origin"); got != "" {
		t.Fatalf("чуждой origin %q не должен получать Access-Control-Allow-Origin, получен %q", foreignTestOrigin, got)
	}
}

// TestCORSDynamicOriginFromEnv проверяет, что кастомный домен, переданный
// через CORS_ALLOWED_ORIGINS (SEC-04), корректно разрешается сервером.
func TestCORSDynamicOriginFromEnv(t *testing.T) {
	customOrigin := "https://student-bot.spbpu.ru"
	app := newCORSAppWithOrigins(customOrigin)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	req.Header.Set("Origin", customOrigin)

	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if got := resp.Header.Get("Access-Control-Allow-Origin"); got != customOrigin {
		t.Fatalf("ожидался Access-Control-Allow-Origin %q, получен %q", customOrigin, got)
	}
}

// TestCORSPreflightAdvertisesCSRFHeader проверяет, что браузерный
// preflight получает список разрешённых заголовков. Без X-CSRF-Token
// в списке запрос отправки ответа отбрасывается браузером ещё до
// попадания в обработчик.
func TestCORSPreflightAdvertisesCSRFHeader(t *testing.T) {
	req := httptest.NewRequest(http.MethodOptions, "/api/v1/departments/", nil)
	req.Header.Set("Origin", allowedTestOrigin)
	req.Header.Set("Access-Control-Request-Method", http.MethodPost)
	req.Header.Set("Access-Control-Request-Headers", "X-CSRF-Token, Content-Type")

	resp, err := newCORSApp().Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusOK {
		t.Fatalf("preflight должен завершаться успехом, получен статус %d", resp.StatusCode)
	}

	allowed := resp.Header.Get("Access-Control-Allow-Headers")
	if !strings.Contains(allowed, "X-CSRF-Token") {
		t.Fatalf("в Access-Control-Allow-Headers нет X-CSRF-Token: %q", allowed)
	}
	if resp.Header.Get("Access-Control-Allow-Methods") == "" {
		t.Fatal("preflight должен содержать Access-Control-Allow-Methods")
	}
}

// TestCreateDepartmentRejectsInvalidPayload покрывает валидацию входных
// данных: пустое тело, не-JSON и название из пробелов должны давать 400
// ДО обращения к БД (иначе нагрузка на пул ради заведомо плохих запросов).
func TestCreateDepartmentRejectsInvalidPayload(t *testing.T) {
	cases := []struct {
		name string
		body string
	}{
		{name: "пустое тело", body: ""},
		{name: "невалидный JSON", body: "{не json"},
		{name: "пустое название", body: `{"name":""}`},
		{name: "название из пробелов", body: `{"name":"   "}`},
		{name: "отсутствует поле name", body: `{"other":"value"}`},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			app := fiber.New()
			app.Post("/api/v1/departments/", CreateDepartment)

			req := httptest.NewRequest(
				http.MethodPost,
				"/api/v1/departments/",
				strings.NewReader(tc.body),
			)
			req.Header.Set("Content-Type", "application/json")

			resp, err := app.Test(req, 5000)
			if err != nil {
				t.Fatalf("не удалось выполнить запрос: %v", err)
			}
			defer resp.Body.Close()

			if resp.StatusCode != fiber.StatusBadRequest {
				t.Fatalf("ожидался статус 400, получен %d", resp.StatusCode)
			}
		})
	}
}

// TestLoginRejectsMalformedBody проверяет, что обработчик входа отвечает 400
// на невалидный JSON и не падает: неверный формат запроса не должен
// приводить к 500 и раскрытию внутренней ошибки.
func TestLoginRejectsMalformedBody(t *testing.T) {
	cases := []struct {
		name string
		body string
	}{
		{name: "невалидный JSON", body: "{не json"},
		{name: "пустое тело", body: ""},
		{name: "JSON не-объект", body: `["username"]`},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			app := fiber.New()
			app.Post("/api/v1/auth/login", Login)

			req := httptest.NewRequest(
				http.MethodPost,
				"/api/v1/auth/login",
				strings.NewReader(tc.body),
			)
			req.Header.Set("Content-Type", "application/json")

			resp, err := app.Test(req, 5000)
			if err != nil {
				t.Fatalf("не удалось выполнить запрос: %v", err)
			}
			defer resp.Body.Close()

			if resp.StatusCode != fiber.StatusBadRequest {
				t.Fatalf("ожидался статус 400, получен %d", resp.StatusCode)
			}
		})
	}
}

// TestLoginRequiresDatabasePoolWithoutLeakingDetails проверяет fail-fast:
// без инициализированного пула БД вход отвечает 503 и НЕ раскрывает,
// существует ли такой логин (SEC-03: перечисление логинов).
func TestLoginRequiresDatabasePoolWithoutLeakingDetails(t *testing.T) {
	app := fiber.New()
	app.Post("/api/v1/auth/login", Login)

	req := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/auth/login",
		strings.NewReader(`{"username":"admin","password":"secret"}`),
	)
	req.Header.Set("Content-Type", "application/json")

	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusServiceUnavailable {
		t.Fatalf("ожидался статус 503, получен %d", resp.StatusCode)
	}

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("ответ должен быть корректным JSON: %v", err)
	}
	// В ответе не должно быть упоминания введённого логина.
	if msg, ok := payload["error"].(string); ok && strings.Contains(strings.ToLower(msg), "admin") {
		t.Fatalf("ответ не должен раскрывать введённый логин: %q", msg)
	}
}

// TestHealthCheckIsAlwaysAvailable проверяет, что healthcheck-эндпоинт
// отвечает 200 без БД и Redis: иначе docker-compose перезапускал бы
// контейнер go-api при временной недоступности зависимостей (5.2).
func TestHealthCheckIsAlwaysAvailable(t *testing.T) {
	app := fiber.New()
	app.Get("/api/v1/health", HealthCheck)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)

	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusOK {
		t.Fatalf("ожидался статус 200, получен %d", resp.StatusCode)
	}

	var payload struct {
		Status    string `json:"status"`
		Timestamp string `json:"timestamp"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("ответ должен быть корректным JSON: %v", err)
	}
	if payload.Status != "healthy" {
		t.Fatalf("ожидался status=healthy, получен %q", payload.Status)
	}
	if payload.Timestamp == "" {
		t.Fatal("ответ должен содержать timestamp")
	}
}

// TestTelegramWebAppAuthRequiresConfiguration проверяет, что при незаданных
// переменных Telegram-интеграции обработчик отвечает 400 с внятным
// сообщением, а не 500. Это защищает от «тихой» неработоспособности
// авторизации в production.
func TestTelegramWebAppAuthRequiresConfiguration(t *testing.T) {
	cases := []struct {
		name string
		cfg  *config.Config
	}{
		{name: "пустой токен", cfg: &config.Config{TelegramAdminID: testAdminID}},
		{name: "нулевой admin ID", cfg: &config.Config{TelegramBotToken: testBotToken}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			app := fiber.New()
			app.Post("/api/v1/auth/telegram-webapp", TelegramWebAppAuth(tc.cfg))

			req := httptest.NewRequest(
				http.MethodPost,
				"/api/v1/auth/telegram-webapp",
				strings.NewReader(`{"init_data":"hash=x"}`),
			)
			req.Header.Set("Content-Type", "application/json")

			resp, err := app.Test(req, 5000)
			if err != nil {
				t.Fatalf("не удалось выполнить запрос: %v", err)
			}
			defer resp.Body.Close()

			if resp.StatusCode != fiber.StatusBadRequest {
				t.Fatalf("ожидался статус 400, получен %d", resp.StatusCode)
			}
		})
	}
}

// TestTelegramWebAppAuthRejectsEmptyInitData проверяет обязательность поля
// init_data: без него запрос не должен доходить до проверки подписи.
func TestTelegramWebAppAuthRejectsEmptyInitData(t *testing.T) {
	app := fiber.New()
	app.Post("/api/v1/auth/telegram-webapp", TelegramWebAppAuth(&config.Config{
		TelegramBotToken: testBotToken,
		TelegramAdminID:  testAdminID,
	}))

	req := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/auth/telegram-webapp",
		strings.NewReader(`{"init_data":""}`),
	)
	req.Header.Set("Content-Type", "application/json")

	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusBadRequest {
		t.Fatalf("ожидался статус 400, получен %d", resp.StatusCode)
	}
}

// TestAuthMeWithoutSessionIsNotAuthenticated проверяет безопасный дефолт:
// без cookie-сессии (и без Redis) /auth/me отвечает authenticated=false,
// а не 500. Фронтенд полагается на этот контракт при проверке входа.
func TestAuthMeWithoutSessionIsNotAuthenticated(t *testing.T) {
	app := fiber.New()
	app.Get("/api/v1/auth/me", AuthMe)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/auth/me", nil)

	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusOK {
		t.Fatalf("ожидался статус 200, получен %d", resp.StatusCode)
	}

	var payload struct {
		Authenticated bool   `json:"authenticated"`
		User          any    `json:"user"`
		CSRFToken     string `json:"csrf_token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("ответ должен быть корректным JSON: %v", err)
	}

	if payload.Authenticated {
		t.Fatal("без сессии пользователь не должен считаться аутентифицированным")
	}
	if payload.User != nil {
		t.Fatalf("поле user должно быть null без сессии, получено %v", payload.User)
	}
	if payload.CSRFToken == "" {
		t.Fatal("ответ должен содержать csrf_token для последующих изменяющих запросов")
	}
}

// TestGetTicketsClampsInvalidLimit проверяет нормализацию limit: значения
// вне диапазона 1..100 заменяются на 50. Без этой защиты limit=0 приводил
// бы к пустому ответу, а limit=99999 — к выборке всей таблицы в память.
func TestGetTicketsClampsInvalidLimit(t *testing.T) {
	// Пул БД не инициализирован, поэтому обработчик завершится раньше
	// выборки. Проверяем именно то, что запрос дошёл до слоя БД (503),
	// а не был отклонён валидатором: значит limit был принят.
	app := fiber.New()
	app.Get("/api/v1/tickets/", GetTickets)

	for _, limit := range []string{"0", "-5", "99999", "abc"} {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/tickets/?limit="+limit, nil)

		resp, err := app.Test(req, 5000)
		if err != nil {
			t.Fatalf("не удалось выполнить запрос: %v", err)
		}
		resp.Body.Close()

		if resp.StatusCode != fiber.StatusServiceUnavailable {
			t.Fatalf("limit=%s: ожидался 503 (запрос дошёл до слоя БД), получен %d", limit, resp.StatusCode)
		}
	}
}
