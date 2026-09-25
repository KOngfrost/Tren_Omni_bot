// Package metrics регистрирует Prometheus-метрики HTTP-слоя Go API.
//
// Метрики собираются в изолированном реестре (не prometheus.DefaultRegisterer),
// поэтому пакет можно поднимать многократно в одном процессе (go test в
// одном бинарнике) без паники «duplicate metrics collector registration».
package metrics

import (
	"strconv"
	"strings"
	"time"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/adaptor"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// namespaceMetrics — префикс всех метрик сервиса. Совпадает с
// одноимёнными метриками Python-панели, чтобы обе части системы
// выглядели однородно в Grafana.
const namespaceMetrics = "oss_bot"

// metricsPath — путь эндпоинта метрик. Объявлен константой, потому что
// его используют и Middleware (исключение самоподсчёта), и
// RegisterMetricsRoute (монтирование).
const metricsPath = "/metrics"

// bucket — границы гистограммы времени ответа (секунды).
// Верхняя граница 10s совпадает с WriteTimeout самого Fiber: всё, что
// дольше, приведёт к разрыву соединения, измерять точнее нет смысла.
// Мелкие интервалы вокруг 5-50 мс дают точное p95 на типовых запросах.
var bucket = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}

// registry — изолированный реестр метрик вместо prometheus.DefaultRegisterer.
// Причина: go test поднимает приложение многократно в одном процессе.
// Регистрация в глобальном дефолтном реестре привела бы к панике
// «duplicate metrics collector registration attempted» уже на втором
// вызове NewInstrumentedApp. Изолированный реестр делает тесты независимыми.
var registry = prometheus.NewRegistry()

// Счётчики и гистограммы разделены по смыслу:
//   - httpRequestsTotal — счётчик запросов (rate() даёт RPS);
//   - httpRequestDuration — гистограмма (histogram_quantile даёт p95/p99).
//
// In-flight и размер ответа дополняют картину: первая показывает
// насыщение event loop, вторая — объём трафика.
var (
	httpRequestsTotal = promauto.With(registry).NewCounterVec(
		prometheus.CounterOpts{
			Namespace: namespaceMetrics,
			Subsystem: "go_api",
			Name:      "http_requests_total",
			Help:      "Число HTTP-запросов, обработанных Go API, по методу, пути и коду ответа.",
		},
		[]string{"method", "path", "status"},
	)

	httpRequestDuration = promauto.With(registry).NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: namespaceMetrics,
			Subsystem: "go_api",
			Name:      "http_request_duration_seconds",
			Help:      "Время обработки HTTP-запроса Go API в секундах.",
			Buckets:   bucket,
		},
		[]string{"method", "path"},
	)

	httpRequestsInFlight = promauto.With(registry).NewGauge(
		prometheus.GaugeOpts{
			Namespace: namespaceMetrics,
			Subsystem: "go_api",
			Name:      "http_requests_in_flight",
			Help:      "Число HTTP-запросов Go API, находящихся в обработке прямо сейчас.",
		},
	)

	httpResponseSize = promauto.With(registry).NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: namespaceMetrics,
			Subsystem: "go_api",
			Name:      "http_response_size_bytes",
			Help:      "Размер HTTP-ответа Go API в байтах.",
			Buckets:   prometheus.ExponentialBuckets(64, 4, 8),
		},
		[]string{"method", "path"},
	)
)

// Middleware возвращает fiber.Handler, регистрирующий метрики каждого запроса.
func Middleware() fiber.Handler {
	return func(c *fiber.Ctx) error {
		// Скрейп метрик не учитываем: иначе эндпоинт раздувал бы
		// счётчики и зависел бы от собственной метрики (а in-flight
		// никогда не опускался бы до нуля из-за самого себя).
		if c.Path() == metricsPath {
			return c.Next()
		}

		start := time.Now()
		path := routePattern(c)
		method := c.Method()

		httpRequestsInFlight.Inc()
		// defer гарантирует уменьшение gauge и при panic, и при ошибке,
		// возвращённой обработчиком: «зависшие» in-flight невозможны.
		defer httpRequestsInFlight.Dec()

		err := c.Next()

		status := c.Response().StatusCode()
		if err != nil {
			// Ошибка из обработчика ещё не превращена в ответ Fiber:
			// статус здесь может быть 200, хотя клиент получит 500.
			// Для метрик это важно — иначе 5xx не попадут в графики.
			if fe, ok := err.(*fiber.Error); ok {
				status = fe.Code
			} else {
				status = fiber.StatusInternalServerError
			}
		}

		httpRequestsTotal.WithLabelValues(method, path, strconv.Itoa(status)).Inc()
		httpRequestDuration.WithLabelValues(method, path).Observe(time.Since(start).Seconds())
		httpResponseSize.WithLabelValues(method, path).Observe(float64(len(c.Response().Body())))

		return err
	}
}

// routePattern возвращает шаблон маршрута вместо фактического пути.
//
// Почему нельзя просто взять c.Route().Path: middleware, зарегистрированный
// через app.Use, выполняется ДО того, как роутер сопоставил запрос с
// конечным обработчиком, поэтому c.Route() отдаёт путь самого middleware
// ("/") для всех запросов подряд. Это ловушка: в тестах метрики схлопывались
// в единственную серию path="/".
//
// Вместо этого путь нормализуется по сегментам: идентификаторы заменяются
// на «:id», а неизвестные глубокие пути схлопываются. Так cardinality
// остаётся конечной и в обычном случае, и для 404.
func routePattern(c *fiber.Ctx) string {
	return normalizeRoutePath(c.Path())
}

// normalizeRoutePath приводит фактический путь к шаблону с ограниченной
// cardinality.
//
// Правила:
//   * сегмент-идентификатор (число, UUID, длинный hex) → «:id»;
//   * путь глубже maxPathSegments сегментов после префикса /api/v1/
//     схлопывается в «/api/v1/<первый>/*», чтобы произвольные URL
//     из интернета не плодили временные серии.
func normalizeRoutePath(path string) string {
	if path == "" {
		return "/"
	}
	// Нормализуем слэши: /api/v1/tickets/ и /api/v1/tickets обязаны
	// попадать в одну серию, иначе удваивается число метрик.
	trimmed := strings.TrimRight(path, "/")
	if trimmed == "" {
		return "/"
	}

	segments := strings.Split(trimmed, "/")
	prefixLen := 1 // пустой ведущий сегмент от ведущего слэша
	if len(segments) > 1 && segments[1] == "api" && len(segments) > 2 && segments[2] == "v1" {
		prefixLen = 3
	}

	body := segments[prefixLen:]
	// Схлопывание слишком глубоких неизвестных путей.
	if len(body) > maxPathSegments {
		head := append([]string{}, segments[:prefixLen]...)
		head = append(head, body[0], "*")
		return strings.Join(head, "/")
	}

	for i := prefixLen; i < len(segments); i++ {
		if isIdentifierSegment(segments[i]) {
			segments[i] = ":id"
		}
	}
	return strings.Join(segments, "/")
}

// maxPathSegments — сколько сегментов после /api/v1/ различаем.
// Эндпоинтов больше десятка; всё, что длиннее, схлопывается.
const maxPathSegments = 3

// isIdentifierSegment определяет, похож ли сегмент на идентификатор.
func isIdentifierSegment(segment string) bool {
	if segment == "" {
		return false
	}
	// Чистое число — самый частый случай (id заявки, отдела, события).
	if _, err := strconv.Atoi(segment); err == nil {
		return true
	}
	// UUID в каноническом виде 8-4-4-4-12.
	if isUUID(segment) {
		return true
	}
	// Длинная hex-строка без дефисов (хеш токена, session id).
	if len(segment) >= 16 && isHex(segment) {
		return true
	}
	return false
}

// uuidLen — длина UUID в каноническом виде с дефисами.
const uuidLen = 36

// isUUID проверяет канонический вид UUID: 8-4-4-4-12 шестнадцатеричных
// цифр, разделённых дефисами.
//
// Реализовано вручную вместо google/uuid: лишняя зависимость ради проверки
// формата не нужна, а формат UUID стабилен и задан RFC 4122.
func isUUID(s string) bool {
	// positions дефисов в строке длиной 36: 8, 13, 18, 23.
	if len(s) != uuidLen {
		return false
	}
	for _, i := range [...]int{8, 13, 18, 23} {
		if s[i] != '-' {
			return false
		}
	}
	// Убираем дефисы и проверяем оставшиеся 32 символа.
	var hexOnly strings.Builder
	for i := range len(s) {
		if s[i] == '-' {
			continue
		}
		hexOnly.WriteByte(s[i])
	}
	return isHex(hexOnly.String())
}

// isHex проверяет, что строка состоит только из шестнадцатеричных цифр.
func isHex(s string) bool {
	if s == "" {
		return false
	}
	for i := range len(s) {
		c := s[i]
		isDigit := c >= '0' && c <= '9'
		isHexLetter := (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
		if !isDigit && !isHexLetter {
			return false
		}
	}
	return true
}

// RegisterMetricsRoute монтирует эндпоинт /metrics по указанному пути.
//
// Эндпоинт намеренно НЕ проходит через CSRF-защиту и сам не попадает
// в httpRequestsTotal: иначе каждый скрейп Prometheus раздувал бы
// счётчики и зависел бы от собственной метрики. Наружу эндпоинт не
// публикуется — Prometheus ходит по внутренней docker-сети.
func RegisterMetricsRoute(app *fiber.App, path string) {
	// promhttp отдаёт net/http-обработчик, а Fiber работает поверх
	// fasthttp и ждёт fiber.Handler. adaptor.HTTPHandler переводит
	// одно в другое: он оборачивает http.Handler так, что Fiber
	// вызывает его через fasthttpadaptor.NewRequestCtx, корректно
	// перенося заголовки, статус и тело в fasthttp-ответ.
	promHandler := promhttp.HandlerFor(registry, promhttp.HandlerOpts{
		// HTTPErrorOnError возвращает 500 при ошибке сбора вместо
		// молчаливого пустого ответа — иначе Prometheus счёл бы
		// неисправность метрик успешным скрейпом.
		ErrorLog:      nil,
		ErrorHandling: promhttp.HTTPErrorOnError,
	})

	app.Get(path, adaptor.HTTPHandler(promHandler))
}

