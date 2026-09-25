package metrics

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gofiber/fiber/v2"
)

// newInstrumentedApp собирает приложение с metrics.Middleware() и
// набором маршрутов, покрывающих успех, ошибку и 404.
func newInstrumentedApp() *fiber.App {
	app := fiber.New()
	app.Use(Middleware())
	app.Get("/api/v1/health", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "healthy"})
	})
	app.Get("/api/v1/tickets/:id", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"id": c.Params("id")})
	})
	app.Get("/api/v1/boom", func(c *fiber.Ctx) error {
		return fiber.NewError(fiber.StatusInternalServerError, "boom")
	})
	RegisterMetricsRoute(app, "/metrics")
	return app
}

// doRequest выполняет запрос и возвращает статус-код ответа.
func doRequest(t *testing.T, app *fiber.App, method, path string) int {
	t.Helper()
	req := httptest.NewRequest(method, path, nil)
	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось выполнить запрос %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	return resp.StatusCode
}

// fetchMetrics возвращает тело эндпоинта /metrics.
func fetchMetrics(t *testing.T, app *fiber.App) string {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	resp, err := app.Test(req, 5000)
	if err != nil {
		t.Fatalf("не удалось получить /metrics: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/metrics должен отвечать 200, получен %d", resp.StatusCode)
	}

	var buf bytes.Buffer
	if _, err := buf.ReadFrom(resp.Body); err != nil {
		t.Fatalf("не удалось прочитать тело /metrics: %v", err)
	}
	return buf.String()
}

// TestMetricsEndpointExposesCounters проверяет базовый контракт:
// /metrics отвечает 200 и содержит объявленные метрики.
func TestMetricsEndpointExposesCounters(t *testing.T) {
	app := newInstrumentedApp()
	doRequest(t, app, http.MethodGet, "/api/v1/health")

	body := fetchMetrics(t, app)

	expected := []string{
		"oss_bot_go_api_http_requests_total",
		"oss_bot_go_api_http_request_duration_seconds",
		"oss_bot_go_api_http_requests_in_flight",
		"oss_bot_go_api_http_response_size_bytes",
	}
	for _, name := range expected {
		if !strings.Contains(body, name) {
			t.Fatalf("метрика %s отсутствует в выводе /metrics:\n%s", name, body)
		}
	}
}

// TestMetricsRecordsStatusFromFiberError проверяет, что ошибка,
// возвращённая обработчиком как fiber.Error, попадает в метрику с
// корректным кодом 500, а не с дефолтными 200.
func TestMetricsRecordsStatusFromFiberError(t *testing.T) {
	app := newInstrumentedApp()

	if code := doRequest(t, app, http.MethodGet, "/api/v1/boom"); code != http.StatusInternalServerError {
		t.Fatalf("ожидался статус 500, получен %d", code)
	}

	body := fetchMetrics(t, app)
	if !strings.Contains(body, `oss_bot_go_api_http_requests_total{method="GET",path="/api/v1/boom",status="500"}`) {
		t.Fatalf("в метриках нет счётчика с status=\"500\" для /api/v1/boom:\n%s", body)
	}
}

// TestMetricsUseRouteTemplateNotRawPath — защита от взрыва cardinality:
// путь с идентификатором обязан попасть в метрику как шаблон :id,
// иначе каждая заявка создавала бы новую временную серию.
func TestMetricsUseRouteTemplateNotRawPath(t *testing.T) {
	app := newInstrumentedApp()

	if code := doRequest(t, app, http.MethodGet, "/api/v1/tickets/173"); code != http.StatusOK {
		t.Fatalf("ожидался статус 200, получен %d", code)
	}

	body := fetchMetrics(t, app)

	if !strings.Contains(body, `path="/api/v1/tickets/:id"`) {
		t.Fatalf("метрика должна использовать шаблон :id:\n%s", body)
	}
	if strings.Contains(body, `path="/api/v1/tickets/173"`) {
		t.Fatalf("сырой путь с идентификатором попал в метрики (cardinality взорвётся):\n%s", body)
	}
}

// TestMetricsDistinguishEndpoints проверяет, что разные эндпоинты не
// сливаются в одну серию: без различения аварию на одном маршруте
// невозможно увидеть отдельно от остального.
func TestMetricsDistinguishEndpoints(t *testing.T) {
	app := newInstrumentedApp()

	doRequest(t, app, http.MethodGet, "/api/v1/health")
	doRequest(t, app, http.MethodGet, "/api/v1/boom")

	body := fetchMetrics(t, app)

	for _, want := range []string{`path="/api/v1/health"`, `path="/api/v1/boom"`} {
		if !strings.Contains(body, want) {
			t.Fatalf("в метриках нет отдельной серии %s:\n%s", want, body)
		}
	}
}

// TestNormalizeRoutePath проверяет нормализацию путей: идентификаторы
// должны схлопываться, чтобы произвольные URL не плодили серии.
func TestNormalizeRoutePath(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{in: "", want: "/"},
		{in: "/", want: "/"},
		{in: "/api/v1", want: "/api/v1"},
		// Идентификаторы заменяются на :id.
		{in: "/api/v1/tickets/173", want: "/api/v1/tickets/:id"},
		{in: "/api/v1/tickets/173/", want: "/api/v1/tickets/:id"},
		{in: "/api/v1/tickets/9968a39f-0d83-408c-a541-a7b583a10ea3", want: "/api/v1/tickets/:id"},
		{in: "/api/v1/tickets/deadbeefdeadbeef01", want: "/api/v1/tickets/:id"},
		// Обычные сегменты не трогаются.
		{in: "/api/v1/health", want: "/api/v1/health"},
		{in: "/api/v1/departments", want: "/api/v1/departments"},
		// Слишком глубокие пути схлопываются.
		{in: "/api/v1/tickets/173/messages/9/extra", want: "/api/v1/tickets/*"},
	}

	for _, tc := range cases {
		if got := normalizeRoutePath(tc.in); got != tc.want {
			t.Errorf("normalizeRoutePath(%q) = %q, ожидалось %q", tc.in, got, tc.want)
		}
	}
}

// TestIsIdentifierSegment проверяет распознавание идентификаторов.
func TestIsIdentifierSegment(t *testing.T) {
	cases := []struct {
		in   string
		want bool
	}{
		{in: "173", want: true},
		{in: "0", want: true},
		{in: "3e786255-e64d-4f2a-9b1c-0d83e6f70819", want: true},
		{in: "deadbeefdeadbeef01", want: true},
		{in: "health", want: false},
		{in: "departments", want: false},
		{in: "not-a-uuid", want: false},
		{in: "", want: false},
	}

	for _, tc := range cases {
		if got := isIdentifierSegment(tc.in); got != tc.want {
			t.Errorf("isIdentifierSegment(%q) = %v, ожидалось %v", tc.in, got, tc.want)
		}
	}
}

// TestInFlightGaugeReturnsToZero проверяет, что gauge не «залипает»
// после завершения запросов: иначе алерт GoApiEventLoopSaturated
// срабатывал бы ложно.
func TestInFlightGaugeReturnsToZero(t *testing.T) {
	app := fiber.New()
	app.Use(Middleware())
	app.Get("/api/v1/health", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "healthy"})
	})
	RegisterMetricsRoute(app, "/metrics")

	for range 5 {
		doRequest(t, app, http.MethodGet, "/api/v1/health")
	}

	// Скрейп метрик выполняется последним и сам проходит через
	// middleware, поэтому к моменту чтения значения gauge все
	// предыдущие запросы уже завершены и уменьшили счётчик.
	body := fetchMetrics(t, app)

	if !strings.Contains(body, "oss_bot_go_api_http_requests_in_flight 0") {
		t.Fatalf("in-flight должен вернуться к нулю после завершения запросов:\n%s", body)
	}
}
