package handlers

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"student_bot/backend_go/internal/config"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/limiter"
)

const (
	testBotToken       = "123456:TEST-BOT-TOKEN"
	testAdminID  int64 = 4242
)

// buildInitData формирует корректно подписанный init_data Telegram WebApp.
func buildInitData(t *testing.T, botToken string, params map[string]string) string {
	t.Helper()

	keys := make([]string, 0, len(params))
	for key := range params {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	var builder strings.Builder
	for i, key := range keys {
		if i > 0 {
			builder.WriteString("\n")
		}
		builder.WriteString(key)
		builder.WriteString("=")
		builder.WriteString(params[key])
	}

	secretHmac := hmac.New(sha256.New, []byte("WebAppData"))
	secretHmac.Write([]byte(botToken))
	secretKey := secretHmac.Sum(nil)

	dataHmac := hmac.New(sha256.New, secretKey)
	dataHmac.Write([]byte(builder.String()))

	params["hash"] = hex.EncodeToString(dataHmac.Sum(nil))

	values := url.Values{}
	for key, value := range params {
		values.Set(key, value)
	}
	return values.Encode()
}

func newAuthApp() *fiber.App {
	app := fiber.New()
	app.Post("/api/v1/auth/telegram-webapp", TelegramWebAppAuth(&config.Config{
		TelegramBotToken: testBotToken,
		TelegramAdminID:  testAdminID,
	}))
	return app
}

func postInitData(t *testing.T, app *fiber.App, initData string) int {
	t.Helper()

	body, err := json.Marshal(map[string]string{"init_data": initData})
	if err != nil {
		t.Fatalf("не удалось сериализовать запрос: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/telegram-webapp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")

	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	return resp.StatusCode
}

func adminUserJSON() string {
	return fmt.Sprintf(`{"id":%d,"first_name":"Admin"}`, testAdminID)
}

func TestTelegramWebAppAuthAcceptsFreshInitData(t *testing.T) {
	initData := buildInitData(t, testBotToken, map[string]string{
		"auth_date": strconv.FormatInt(time.Now().Unix(), 10),
		"user":      adminUserJSON(),
	})

	if code := postInitData(t, newAuthApp(), initData); code != http.StatusOK {
		t.Fatalf("свежий init_data должен приниматься, получен статус %d", code)
	}
}

func TestTelegramWebAppAuthRejectsExpiredInitData(t *testing.T) {
	// SEC-01: перехваченный init_data старше 24 часов не должен приниматься повторно.
	initData := buildInitData(t, testBotToken, map[string]string{
		"auth_date": strconv.FormatInt(time.Now().Add(-25*time.Hour).Unix(), 10),
		"user":      adminUserJSON(),
	})

	if code := postInitData(t, newAuthApp(), initData); code != http.StatusUnauthorized {
		t.Fatalf("устаревший init_data должен отклоняться (401), получен статус %d", code)
	}
}

func TestTelegramWebAppAuthRejectsFutureInitData(t *testing.T) {
	// SEC-01: auth_date из будущего — признак подделки/рассинхронизации времени.
	initData := buildInitData(t, testBotToken, map[string]string{
		"auth_date": strconv.FormatInt(time.Now().Add(10*time.Minute).Unix(), 10),
		"user":      adminUserJSON(),
	})

	if code := postInitData(t, newAuthApp(), initData); code != http.StatusUnauthorized {
		t.Fatalf("init_data с auth_date в будущем должен отклоняться (401), получен статус %d", code)
	}
}

func TestTelegramWebAppAuthRejectsMissingAuthDate(t *testing.T) {
	initData := buildInitData(t, testBotToken, map[string]string{
		"user": adminUserJSON(),
	})

	if code := postInitData(t, newAuthApp(), initData); code != http.StatusUnauthorized {
		t.Fatalf("init_data без auth_date должен отклоняться (401), получен статус %d", code)
	}
}

func TestTelegramWebAppAuthRejectsInvalidSignature(t *testing.T) {
	initData := buildInitData(t, "123456:ANOTHER-TOKEN", map[string]string{
		"auth_date": strconv.FormatInt(time.Now().Unix(), 10),
		"user":      adminUserJSON(),
	})

	if code := postInitData(t, newAuthApp(), initData); code != http.StatusUnauthorized {
		t.Fatalf("подпись чужим токеном должна отклоняться (401), получен статус %d", code)
	}
}

func TestTelegramWebAppAuthRejectsForeignTelegramUser(t *testing.T) {
	initData := buildInitData(t, testBotToken, map[string]string{
		"auth_date": strconv.FormatInt(time.Now().Unix(), 10),
		"user":      `{"id":777777,"first_name":"Intruder"}`,
	})

	if code := postInitData(t, newAuthApp(), initData); code != http.StatusForbidden {
		t.Fatalf("чужой Telegram ID должен получать 403, получен статус %d", code)
	}
}

// TestHandlersWithoutDatabasePoolReturn503 проверяет защиту от nil-pointer panic:
// при неинициализированном пуле БД обработчики отвечают 503, а не падают.
func TestHandlersWithoutDatabasePoolReturn503(t *testing.T) {
	app := fiber.New()
	app.Get("/stats", GetStats(&config.Config{}))
	app.Get("/departments/", GetDepartments)
	app.Get("/tickets/", GetTickets)

	for _, path := range []string{"/stats", "/departments/", "/tickets/"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		resp, err := app.Test(req, 5000)
		if err != nil {
			t.Fatalf("запрос %s завершился ошибкой: %v", path, err)
		}
		resp.Body.Close()

		if resp.StatusCode != fiber.StatusServiceUnavailable {
			t.Fatalf("запрос %s: ожидался статус 503, получен %d", path, resp.StatusCode)
		}
	}
}

// newCSRFApp собирает приложение с CSRFProtect и dummy POST-обработчиком.
func newCSRFApp() *fiber.App {
	app := fiber.New()
	app.Use(CSRFProtect())
	app.Post("/api/v1/test", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"ok": true})
	})
	return app
}

// TestCSRFProtectSetsCookieOnSafeMethod проверяет, что безопасный метод
// устанавливает cookie с криптостойким CSRF-токеном (SEC-02).
func TestCSRFProtectSetsCookieOnSafeMethod(t *testing.T) {
	app := newCSRFApp()
	app.Get("/api/v1/test", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"csrf": currentCSRFToken(c)})
	})

	req := httptest.NewRequest(http.MethodGet, "/api/v1/test", nil)
	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	setCookie := resp.Header.Get("Set-Cookie")
	if !strings.Contains(setCookie, "csrf_token=") {
		t.Fatalf("ожидалась установка cookie csrf_token, получено: %q", setCookie)
	}
	if strings.Contains(setCookie, "csrf_token=;") {
		t.Fatalf("cookie csrf_token не должна быть пустой: %q", setCookie)
	}
}

// TestCSRFProtectRejectsPostWithoutToken проверяет отклонение POST
// без CSRF-заголовка (SEC-02).
func TestCSRFProtectRejectsPostWithoutToken(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/v1/test", nil)
	resp, err := newCSRFApp().Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("POST без CSRF-токена должен отклоняться (403), получен %d", resp.StatusCode)
	}
}

// TestCSRFProtectRejectsMismatchedToken проверяет отклонение POST,
// когда заголовок не совпадает с cookie (SEC-02).
func TestCSRFProtectRejectsMismatchedToken(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/v1/test", nil)
	req.Header.Set("Cookie", "csrf_token=valid_cookie_token")
	req.Header.Set("X-CSRF-Token", "forged_header_token")

	resp, err := newCSRFApp().Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("несовпадающий CSRF-токен должен отклоняться (403), получен %d", resp.StatusCode)
	}
}

// TestCSRFProtectAcceptsMatchingToken проверяет пропуск POST
// с корректным double-submit токеном (SEC-02).
func TestCSRFProtectAcceptsMatchingToken(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/v1/test", nil)
	req.Header.Set("Cookie", "csrf_token=valid_cookie_token")
	req.Header.Set("X-CSRF-Token", "valid_cookie_token")

	resp, err := newCSRFApp().Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("корректный CSRF-токен должен приниматься (200), получен %d", resp.StatusCode)
	}
}

// TestNewCSRFTokenIsRandomAndStrong проверяет криптостойкость
// генерации токена (SEC-02): непустой, уникальный, достаточная длина.
func TestNewCSRFTokenIsRandomAndStrong(t *testing.T) {
	first, err := newCSRFToken()
	if err != nil {
		t.Fatalf("не удалось сгенерировать токен: %v", err)
	}
	second, err := newCSRFToken()
	if err != nil {
		t.Fatalf("не удалось сгенерировать токен: %v", err)
	}

	if first == "" || len(first) < 32 {
		t.Fatalf("токен слишком короткий: %d символов", len(first))
	}
	if first == second {
		t.Fatal("два сгенерированных токена не должны совпадать")
	}
}

// TestSignItsdangerousAndExtractSessionID проверяет симметричность формирования
// и валидации подписи сессионной cookie itsdangerous.
func TestSignItsdangerousAndExtractSessionID(t *testing.T) {
	secret := "test-secret-key-32-chars-length"
	sessionID := "0123456789abcdef0123456789abcdef"

	signed := SignItsdangerous(sessionID, secret)
	if !strings.HasPrefix(signed, "s_"+sessionID+".") {
		t.Fatalf("подписанный cookie должен начинаться с prefix 's_%s.', получено: %q", sessionID, signed)
	}

	extracted := ExtractSessionID(signed, secret)
	if extracted != sessionID {
		t.Fatalf("ExtractSessionID(%q) = %q, ожидалось %q", signed, extracted, sessionID)
	}

	// Неверный ключ должен приводить к отклонению
	wrongSecret := "wrong-secret-key-32-chars-length"
	if extractedWrong := ExtractSessionID(signed, wrongSecret); extractedWrong != "" {
		t.Fatalf("ExtractSessionID с неверным секретом должен возвращать '', получено %q", extractedWrong)
	}

	// Пустой ключ не должен подписывать
	if emptySigned := SignItsdangerous(sessionID, ""); emptySigned != "" {
		t.Fatalf("SignItsdangerous с пустым ключом должен возвращать '', получено %q", emptySigned)
	}
}

// TestHealthCheckReadinessProbeReturns503WhenDependenciesDown проверяет контракт readiness probe:
// при недоступных БД и Redis возвращается 503 Service Unavailable с диагностикой.
func TestHealthCheckReadinessProbeReturns503WhenDependenciesDown(t *testing.T) {
	app := fiber.New()
	app.Get("/health", HealthCheck)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != fiber.StatusServiceUnavailable {
		t.Fatalf("ожидался статус 503, получен %d", resp.StatusCode)
	}

	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("ошибка декодирования ответа: %v", err)
	}

	if body["status"] != "unhealthy" {
		t.Fatalf("ожидался status=unhealthy, получено %v", body["status"])
	}
	if body["database"] != "unavailable" {
		t.Fatalf("ожидался database=unavailable, получено %v", body["database"])
	}
	if body["redis"] != "unavailable" {
		t.Fatalf("ожидался redis=unavailable, получено %v", body["redis"])
	}
}

// TestRateLimiterMiddleware проверяет ограничение частоты запросов.
func TestRateLimiterMiddleware(t *testing.T) {
	app := fiber.New()
	api := app.Group("/api/v1")
	api.Use(limiter.New(limiter.Config{
		Max:        2,
		Expiration: 1 * time.Minute,
		LimitReached: func(c *fiber.Ctx) error {
			return c.Status(fiber.StatusTooManyRequests).JSON(fiber.Map{
				"detail": "Превышен лимит запросов. Пожалуйста, повторите позже.",
			})
		},
	}))
	api.Get("/tickets", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"tickets": []string{}})
	})

	for i := 0; i < 2; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/tickets", nil)
		resp, err := app.Test(req, 5000)
		if err != nil {
			t.Fatalf("запрос %d завершился ошибкой: %v", i+1, err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("запрос %d: ожидался статус 200, получен %d", i+1, resp.StatusCode)
		}
	}

	// 3-й запрос должен превысить лимит и вернуть 429
	req := httptest.NewRequest(http.MethodGet, "/api/v1/tickets", nil)
	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("запрос 3 завершился ошибкой: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("запрос 3: ожидался статус 429 Too Many Requests, получен %d", resp.StatusCode)
	}
}
