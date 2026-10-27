package handlers

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"student_bot/backend_go/internal/config"
	"student_bot/backend_go/internal/database"
	appRedis "student_bot/backend_go/internal/redis"

	"github.com/gofiber/fiber/v2"
	"github.com/jackc/pgx/v5/pgconn"
	"golang.org/x/crypto/bcrypt"
)

// === DTOs ===

type UserSession struct {
	Username     string `json:"username"`
	Role         string `json:"role"`
	WebUserID    *int64 `json:"web_user_id,omitempty"`
	DepartmentID *int64 `json:"department_id,omitempty"`
	TelegramID   int64  `json:"telegram_id,omitempty"`
	Bootstrap    bool   `json:"bootstrap,omitempty"`
}

type DepartmentUsage struct {
	Tickets       int `json:"tickets"`
	Knowledge     int `json:"knowledge"`
	FAQ           int `json:"faq"`
	Events        int `json:"events"`
	Subscriptions int `json:"subscriptions"`
	Admins        int `json:"admins"`
}

type DepartmentItem struct {
	ID        int64            `json:"id"`
	Name      string           `json:"name"`
	CreatedAt *string          `json:"created_at"`
	Usage     *DepartmentUsage `json:"usage,omitempty"`
}

type TicketItem struct {
	ID             int64   `json:"id"`
	Topic          string  `json:"topic"`
	Status         string  `json:"status"`
	DepartmentID   *int64  `json:"department_id"`
	DepartmentName *string `json:"department_name"`
	CreatedAt      *string `json:"created_at"`
}

// === Ограничения безопасности ===

const (
	// SEC-01: максимально допустимый возраст init_data Telegram WebApp
	// (защита от replay-атак устаревшими подписанными данными).
	telegramInitDataMaxAgeSeconds = 86400
	// Допустимое опережение часов клиента (защита от ложных отказов
	// при небольшом расхождении времени клиента и сервера).
	telegramInitDataClockSkew = 30 * time.Second
)

// uniqueViolationCode — код ошибки PostgreSQL "unique_violation".
const uniqueViolationCode = "23505"

// === CSRF-защита (SEC-02) ===

const (
	// Имя cookie и заголовка для double-submit проверки CSRF-токена.
	csrfCookieName = "csrf_token"
	csrfHeaderName = "X-CSRF-Token"
	// Время жизни CSRF-cookie.
	csrfCookieMaxAgeSeconds = 12 * 60 * 60
)

// newCSRFToken генерирует криптографически стойкий токен:
// 32 случайных байта (crypto/rand) в URL-safe Base64.
func newCSRFToken() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}

// currentCSRFToken возвращает действующий CSRF-токен клиента.
// При отсутствии cookie генерирует новый токен и устанавливает cookie
// (не httponly — токен должен быть доступен JS фронтенда).
func currentCSRFToken(c *fiber.Ctx) string {
	if token := c.Cookies(csrfCookieName); token != "" {
		return token
	}
	token, err := newCSRFToken()
	if err != nil {
		log.Printf("SEC-02: не удалось сгенерировать CSRF-токен: %v", err)
		return ""
	}
	isSecure := c.Protocol() == "https" || c.Get("X-Forwarded-Proto") == "https"
	c.Cookie(&fiber.Cookie{
		Name:     csrfCookieName,
		Value:    token,
		Path:     "/",
		MaxAge:   csrfCookieMaxAgeSeconds,
		SameSite: "Lax",
		HTTPOnly: false,
		Secure:   isSecure,
	})
	return token
}

// CSRFProtect проверяет заголовок X-CSRF-Token на всех изменяющих
// эндпоинтах (POST/PUT/DELETE/PATCH) по схеме double-submit cookie:
// заголовок обязан совпадать с cookie, установленной сервером.
// Для безопасных методов (GET/HEAD/OPTIONS) гарантирует наличие
// CSRF-cookie для последующих изменяющих запросов.
func CSRFProtect() fiber.Handler {
	return func(c *fiber.Ctx) error {
		switch c.Method() {
		case fiber.MethodPost, fiber.MethodPut, fiber.MethodDelete, fiber.MethodPatch:
		default:
			_ = currentCSRFToken(c)
			return c.Next()
		}

		cookieToken := c.Cookies(csrfCookieName)
		headerToken := c.Get(csrfHeaderName)
		if cookieToken == "" || headerToken == "" ||
			subtle.ConstantTimeCompare([]byte(cookieToken), []byte(headerToken)) != 1 {
			return c.Status(fiber.StatusForbidden).JSON(fiber.Map{
				"detail": "Недействительный CSRF-токен. Обновите страницу и повторите запрос.",
			})
		}
		return c.Next()
	}
}

// === Защита от перечисления логинов (SEC-03) ===

// dummyPasswordHash — bcrypt-хеш случайного пароля, вычисляемый однократно
// при инициализации пакета. Используется для фиктивного сравнения пароля,
// когда пользователь не найден в БД: время ответа для несуществующего
// логина совпадает с существующим, что исключает timing attack.
var dummyPasswordHash = func() []byte {
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return nil
	}
	hash, err := bcrypt.GenerateFromPassword(random, bcrypt.DefaultCost)
	if err != nil {
		return nil
	}
	return hash
}()

// dummyPasswordCompare выполняет фиктивную проверку пароля с постоянным
// хешем — результат всегда ошибочен и игнорируется, важна только стоимость.
func dummyPasswordCompare(password string) {
	if dummyPasswordHash != nil {
		_ = bcrypt.CompareHashAndPassword(dummyPasswordHash, []byte(password))
	}
}

// === Вспомогательные функции ===

// ensurePool защищает обработчики от nil-pointer panic: если пул БД не инициализирован,
// отправляет клиенту 503 и возвращает false — обработчик обязан вернуть nil.
func ensurePool(c *fiber.Ctx, operation string) bool {
	if database.Pool == nil {
		log.Printf("Критическая ошибка: пул подключений к базе данных не инициализирован (%s)", operation)
		_ = c.Status(fiber.StatusServiceUnavailable).JSON(fiber.Map{
			"error": "База данных недоступна",
		})
		return false
	}
	return true
}

// respondDatabaseError логирует внутреннюю ошибку БД и отдаёт клиенту 500,
// исключая отдачу ложных нулевых данных со статусом 200.
func respondDatabaseError(c *fiber.Ctx, operation string, err error) error {
	log.Printf("Ошибка базы данных (%s): %v", operation, err)
	return c.Status(fiber.StatusInternalServerError).JSON(fiber.Map{
		"error":  "Внутренняя ошибка сервера при обращении к базе данных",
		"detail": "Не удалось выполнить операцию: " + operation,
	})
}

// isUniqueViolation проверяет, что ошибка вызвана нарушением UNIQUE-ограничения.
func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == uniqueViolationCode
}

// HealthCheck выполняет проверку готовности (readiness probe):
// проверяет статус базы данных (database.Pool) и Redis (appRedis.Client).
// Если любая из зависимостей недоступна, возвращает 503 Service Unavailable с диагностикой.
// Если обе зависимости исправны, возвращает 200 OK.
func HealthCheck(c *fiber.Ctx) error {
	ctx, cancel := context.WithTimeout(c.Context(), 2*time.Second)
	defer cancel()

	dbStatus := "ok"
	if database.Pool == nil {
		dbStatus = "unavailable"
	} else if err := database.Pool.Ping(ctx); err != nil {
		log.Printf("HealthCheck DB error: %v", err)
		dbStatus = "unavailable"
	}

	redisStatus := "ok"
	if appRedis.Client == nil {
		redisStatus = "unavailable"
	} else if err := appRedis.Client.Ping(ctx).Err(); err != nil {
		log.Printf("HealthCheck Redis error: %v", err)
		redisStatus = "unavailable"
	}

	if dbStatus != "ok" || redisStatus != "ok" {
		return c.Status(fiber.StatusServiceUnavailable).JSON(fiber.Map{
			"status":    "unhealthy",
			"database":  dbStatus,
			"redis":     redisStatus,
			"timestamp": time.Now().UTC().Format(time.RFC3339),
		})
	}

	return c.Status(fiber.StatusOK).JSON(fiber.Map{
		"status":    "healthy",
		"database":  "ok",
		"redis":     "ok",
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	})
}

// HealthCheckHandler возвращает fiber.Handler для эндпоинта healthcheck.
func HealthCheckHandler(cfg *config.Config) fiber.Handler {
	return HealthCheck
}

func GetStats(cfg *config.Config) fiber.Handler {
	return func(c *fiber.Ctx) error {
		ctx := c.Context()

		if !ensurePool(c, "статистика тикетов") {
			return nil
		}

		var totalTickets, activeTickets int
		if err := database.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM tickets").Scan(&totalTickets); err != nil {
			return respondDatabaseError(c, "подсчёт общего числа тикетов", err)
		}
		if err := database.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM tickets WHERE status IN ('NEW', 'IN_PROGRESS')").Scan(&activeTickets); err != nil {
			return respondDatabaseError(c, "подсчёт активных тикетов", err)
		}

		redisOK := false
		if appRedis.Client != nil {
			redisOK = appRedis.Client.Ping(ctx).Err() == nil
		}

		return c.JSON(fiber.Map{
			"version":            cfg.AppVersion,
			"environment":        "production",
			"redis_connected":    redisOK,
			"sentry_enabled":     false,
			"two_factor_enabled": cfg.TwoFactorEnabled,
			"total_tickets":      totalTickets,
			"active_tickets":     activeTickets,
		})
	}
}

func GetDepartments(c *fiber.Ctx) error {
	ctx := c.UserContext()

	if !ensurePool(c, "список отделов") {
		return nil
	}

	query := `
		SELECT d.id, d.name, COUNT(t.id) AS ticket_count
		FROM departments d
		LEFT JOIN tickets t ON t.department_id = d.id
		GROUP BY d.id, d.name
		ORDER BY d.name ASC
	`
	rows, err := database.Pool.Query(ctx, query)
	if err != nil {
		return respondDatabaseError(c, "выборка отделов", err)
	}
	defer rows.Close()

	depts := make([]DepartmentItem, 0)
	for rows.Next() {
		var d DepartmentItem
		var ticketCount int
		if err := rows.Scan(&d.ID, &d.Name, &ticketCount); err != nil {
			return respondDatabaseError(c, "чтение списка отделов", err)
		}
		d.Usage = &DepartmentUsage{Tickets: ticketCount}
		depts = append(depts, d)
	}
	if err := rows.Err(); err != nil {
		return respondDatabaseError(c, "чтение списка отделов", err)
	}

	return c.JSON(depts)
}

func CreateDepartment(c *fiber.Ctx) error {
	// Если в контексте есть аутентифицированный пользователь, проверяем права суперадминистратора
	if user, ok := c.Locals("user").(*UserSession); ok && user != nil {
		if !IsSuperAdminRole(user.Role) {
			return c.Status(fiber.StatusForbidden).JSON(fiber.Map{
				"detail": "Недостаточно прав для выполнения операции",
			})
		}
	}

	var body struct {
		Name string `json:"name"`
	}
	if err := c.BodyParser(&body); err != nil || strings.TrimSpace(body.Name) == "" {
		return c.Status(400).JSON(fiber.Map{"detail": "Название отдела не может быть пустым"})
	}

	name := strings.TrimSpace(body.Name)
	ctx := c.UserContext()

	if !ensurePool(c, "создание отдела") {
		return nil
	}

	var newID int64
	err := database.Pool.QueryRow(ctx, "INSERT INTO departments (name) VALUES ($1) RETURNING id", name).Scan(&newID)
	if err != nil {
		if isUniqueViolation(err) {
			return c.Status(fiber.StatusBadRequest).JSON(fiber.Map{"detail": "Отдел с таким названием уже существует"})
		}
		// Прочие ошибки БД (нет соединения, нет прав и т.п.) не маскируются под 400.
		return respondDatabaseError(c, "создание отдела", err)
	}

	return c.Status(201).JSON(DepartmentItem{
		ID:    newID,
		Name:  name,
		Usage: &DepartmentUsage{},
	})
}

func GetTickets(c *fiber.Ctx) error {
	ctx := c.UserContext()
	limit := c.QueryInt("limit", 50)
	if limit < 1 || limit > 100 {
		limit = 50
	}

	if !ensurePool(c, "список тикетов") {
		return nil
	}

	var query string
	var args []any

	user, _ := c.Locals("user").(*UserSession)
	if user != nil && !IsSuperAdminRole(user.Role) {
		// Ограничение области видимости оператора (operator scope)
		if user.DepartmentID != nil {
			query = `
				SELECT t.id, COALESCE(t.topic, ''), COALESCE(t.status, 'NEW'), t.department_id, d.name, t.created_at
				FROM tickets t
				LEFT JOIN departments d ON t.department_id = d.id
				WHERE (t.department_id = $2 OR t.department_id IS NULL)
				ORDER BY t.created_at DESC
				LIMIT $1
			`
			args = []any{limit, *user.DepartmentID}
		} else {
			query = `
				SELECT t.id, COALESCE(t.topic, ''), COALESCE(t.status, 'NEW'), t.department_id, d.name, t.created_at
				FROM tickets t
				LEFT JOIN departments d ON t.department_id = d.id
				WHERE t.department_id IS NULL
				ORDER BY t.created_at DESC
				LIMIT $1
			`
			args = []any{limit}
		}
	} else {
		query = `
			SELECT t.id, COALESCE(t.topic, ''), COALESCE(t.status, 'NEW'), t.department_id, d.name, t.created_at
			FROM tickets t
			LEFT JOIN departments d ON t.department_id = d.id
			ORDER BY t.created_at DESC
			LIMIT $1
		`
		args = []any{limit}
	}

	rows, err := database.Pool.Query(ctx, query, args...)
	if err != nil {
		return respondDatabaseError(c, "выборка тикетов", err)
	}
	defer rows.Close()

	tickets := make([]TicketItem, 0)
	for rows.Next() {
		var t TicketItem
		var createdAt *time.Time
		if err := rows.Scan(&t.ID, &t.Topic, &t.Status, &t.DepartmentID, &t.DepartmentName, &createdAt); err != nil {
			return respondDatabaseError(c, "чтение тикета", err)
		}
		if createdAt != nil {
			formatted := createdAt.Format(time.RFC3339)
			t.CreatedAt = &formatted
		}
		tickets = append(tickets, t)
	}
	if err := rows.Err(); err != nil {
		return respondDatabaseError(c, "выборка тикетов", err)
	}

	return c.JSON(tickets)
}

// === Auth Handlers & RBAC ===

// verifyItsdangerousSignature проверяет криптографическую HMAC-подпись значения value против переданной подписи sig.
// Реализует совместимость с itsdangerous / FastAPI Starlette SessionMiddleware:
// 1) itsdangerous по умолчанию: salt="itsdangerous.Signer", key_derivation="django-concat", digest=SHA1
// 2) itsdangerous с salt="signer", key_derivation="django-concat", digest=SHA1
// 3) itsdangerous с digest=SHA256 (default salt)
// 4) itsdangerous с salt="signer", digest=SHA256
// 5) прямой HMAC-SHA256 (без KDF)
// 6) прямой HMAC-SHA1 (без KDF)
func verifyItsdangerousSignature(value, sig, secretKey string) bool {
	if secretKey == "" || sig == "" {
		return false
	}

	sigBytes := []byte(sig)

	// 1. itsdangerous default: salt="itsdangerous.Signer", key_derivation="django-concat", digest=SHA1
	{
		h := sha1.New()
		h.Write([]byte("itsdangerous.Signersigner"))
		h.Write([]byte(secretKey))
		derivedKey := h.Sum(nil)

		mac := hmac.New(sha1.New, derivedKey)
		mac.Write([]byte(value))
		expectedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
		if subtle.ConstantTimeCompare([]byte(expectedSig), sigBytes) == 1 {
			return true
		}
	}

	// 2. itsdangerous с salt="signer", key_derivation="django-concat", digest=SHA1
	{
		h := sha1.New()
		h.Write([]byte("signersigner"))
		h.Write([]byte(secretKey))
		derivedKey := h.Sum(nil)

		mac := hmac.New(sha1.New, derivedKey)
		mac.Write([]byte(value))
		expectedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
		if subtle.ConstantTimeCompare([]byte(expectedSig), sigBytes) == 1 {
			return true
		}
	}

	// 3. itsdangerous с digest=SHA256 (default salt)
	{
		h := sha256.New()
		h.Write([]byte("itsdangerous.Signersigner"))
		h.Write([]byte(secretKey))
		derivedKey := h.Sum(nil)

		mac := hmac.New(sha256.New, derivedKey)
		mac.Write([]byte(value))
		expectedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
		if subtle.ConstantTimeCompare([]byte(expectedSig), sigBytes) == 1 {
			return true
		}
	}

	// 4. itsdangerous с salt="signer", digest=SHA256
	{
		h := sha256.New()
		h.Write([]byte("signersigner"))
		h.Write([]byte(secretKey))
		derivedKey := h.Sum(nil)

		mac := hmac.New(sha256.New, derivedKey)
		mac.Write([]byte(value))
		expectedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
		if subtle.ConstantTimeCompare([]byte(expectedSig), sigBytes) == 1 {
			return true
		}
	}

	// 5. Прямой HMAC-SHA256 (без KDF)
	{
		mac := hmac.New(sha256.New, []byte(secretKey))
		mac.Write([]byte(value))
		expectedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
		if subtle.ConstantTimeCompare([]byte(expectedSig), sigBytes) == 1 {
			return true
		}
	}

	// 6. Прямой HMAC-SHA1 (без KDF)
	{
		mac := hmac.New(sha1.New, []byte(secretKey))
		mac.Write([]byte(value))
		expectedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
		if subtle.ConstantTimeCompare([]byte(expectedSig), sigBytes) == 1 {
			return true
		}
	}

	return false
}

// signItsdangerous вычисляет HMAC-SHA1 подпись в формате itsdangerous (совместимо со Starlette SessionMiddleware).
// Возвращает строку формата "s_<session_id>.<signature>".
func signItsdangerous(sessionID, secretKey string) string {
	if secretKey == "" {
		return ""
	}
	value := sessionID
	if !strings.HasPrefix(value, "s_") {
		value = "s_" + sessionID
	}
	h := sha1.New()
	h.Write([]byte("itsdangerous.Signersigner"))
	h.Write([]byte(secretKey))
	derivedKey := h.Sum(nil)

	mac := hmac.New(sha1.New, derivedKey)
	mac.Write([]byte(value))
	sig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return value + "." + sig
}

// SignItsdangerous экспортирует signItsdangerous для тестов и внешнего использования.
func SignItsdangerous(sessionID, secretKey string) string {
	return signItsdangerous(sessionID, secretKey)
}

// ExtractSessionID проверяет HMAC-подпись cookie itsdangerous (s_<id>.<sig>) с помощью secretKey
// и извлекает чистый идентификатор сессии.
// Если подпись не совпадает или cookie повреждён/не подписан, возвращается пустая строка.
func ExtractSessionID(rawCookie string, secretKey string) string {
	val := strings.TrimSpace(rawCookie)
	if val == "" || secretKey == "" {
		return ""
	}

	idx := strings.LastIndex(val, ".")
	if idx == -1 {
		return ""
	}

	value := val[:idx]
	sig := val[idx+1:]
	if sig == "" || !strings.HasPrefix(value, "s_") {
		return ""
	}

	if !verifyItsdangerousSignature(value, sig, secretKey) {
		return ""
	}

	return strings.TrimPrefix(value, "s_")
}

// GetUserFromSession возвращает данные пользователя из сессии Redis или валидного сервисного токена.
func GetUserFromSession(c *fiber.Ctx, cfg *config.Config) (*UserSession, error) {
	// 1. Проверяем сервисный токен из заголовка Authorization (Bearer/Token) или X-Auth-Token
	authHeader := c.Get("Authorization")
	var token string
	if strings.HasPrefix(authHeader, "Bearer ") {
		token = strings.TrimSpace(strings.TrimPrefix(authHeader, "Bearer "))
	} else if strings.HasPrefix(authHeader, "Token ") {
		token = strings.TrimSpace(strings.TrimPrefix(authHeader, "Token "))
	}
	if token == "" {
		token = strings.TrimSpace(c.Get("X-Auth-Token"))
	}

	// Бэкдор удалён: SessionSecretKey в Bearer токене больше НЕ даёт прав суперпользователя.
	// Сервисный доступ разрешён ТОЛЬКО через явно заданный отдельный SERVICE_API_KEY.
	if token != "" && cfg != nil && cfg.ServiceAPIKey != "" &&
		subtle.ConstantTimeCompare([]byte(token), []byte(cfg.ServiceAPIKey)) == 1 {
		return &UserSession{
			Username: "system_service",
			Role:     "SUPERADMIN",
		}, nil
	}

	// 2. Ищем сессию в Redis
	sessionCookie := c.Cookies("session")
	var candidateIDs []string

	if sessionCookie != "" && cfg != nil && cfg.SessionSecretKey != "" {
		extracted := ExtractSessionID(sessionCookie, cfg.SessionSecretKey)
		if extracted != "" {
			candidateIDs = append(candidateIDs, extracted)
		}
	}

	if len(candidateIDs) == 0 || appRedis.Client == nil {
		return nil, errors.New("сессия отсутствует")
	}

	ctx := c.Context()
	for _, id := range candidateIDs {
		val, err := appRedis.Client.Get(ctx, "session:"+id).Result()
		if err != nil || val == "" {
			continue
		}

		var sessionData struct {
			User *UserSession `json:"user"`
		}
		if err := json.Unmarshal([]byte(val), &sessionData); err == nil && sessionData.User != nil {
			return sessionData.User, nil
		}
	}

	return nil, errors.New("сессия не найдена или недействительна")
}

// IsSuperAdminRole проверяет роль суперадминистратора.
func IsSuperAdminRole(role string) bool {
	r := strings.ToUpper(strings.TrimSpace(role))
	return r == "SUPERADMIN"
}

// IsOperatorOrAdminRole проверяет роль оператора или администратора.
func IsOperatorOrAdminRole(role string) bool {
	r := strings.ToUpper(strings.TrimSpace(role))
	return r == "SUPERADMIN" || r == "DEPARTMENT_ADMIN" || r == "ADMIN" || r == "OPERATOR"
}

// RequireSuperadmin — middleware проверки прав суперадминистратора (D03).
// Требует валидную сессию или auth-токен. Возвращает 401 при отсутствии
// аутентификации и 403 при нехватке прав.
func RequireSuperadmin(cfg *config.Config) fiber.Handler {
	return func(c *fiber.Ctx) error {
		user, err := GetUserFromSession(c, cfg)
		if err != nil || user == nil {
			return c.Status(fiber.StatusUnauthorized).JSON(fiber.Map{
				"detail": "Не авторизован",
			})
		}
		if !IsSuperAdminRole(user.Role) {
			return c.Status(fiber.StatusForbidden).JSON(fiber.Map{
				"detail": "Недостаточно прав для выполнения операции",
			})
		}
		c.Locals("user", user)
		return c.Next()
	}
}

// RequireOperator — middleware проверки прав оператора/администратора (D03).
// Требует валидную сессию или auth-токен с ролью SUPERADMIN, DEPARTMENT_ADMIN, ADMIN или OPERATOR.
// Возвращает 401 при отсутствии аутентификации и 403 при нехватке прав.
func RequireOperator(cfg *config.Config) fiber.Handler {
	return func(c *fiber.Ctx) error {
		user, err := GetUserFromSession(c, cfg)
		if err != nil || user == nil {
			return c.Status(fiber.StatusUnauthorized).JSON(fiber.Map{
				"detail": "Не авторизован",
			})
		}
		if !IsOperatorOrAdminRole(user.Role) {
			return c.Status(fiber.StatusForbidden).JSON(fiber.Map{
				"detail": "Недостаточно прав для выполнения операции",
			})
		}
		c.Locals("user", user)
		return c.Next()
	}
}

func AuthMe(cfg *config.Config) fiber.Handler {
	return func(c *fiber.Ctx) error {
		user, err := GetUserFromSession(c, cfg)
		if err != nil || user == nil {
			return c.JSON(fiber.Map{"authenticated": false, "user": nil, "csrf_token": currentCSRFToken(c)})
		}

		return c.JSON(fiber.Map{
			"authenticated": true,
			"user":          user,
			"csrf_token":    currentCSRFToken(c),
		})
	}
}

func TelegramWebAppAuth(cfg *config.Config) fiber.Handler {
	return func(c *fiber.Ctx) error {
		var body struct {
			InitData string `json:"init_data"`
		}
		if err := c.BodyParser(&body); err != nil || body.InitData == "" {
			return c.Status(400).JSON(fiber.Map{"detail": "init_data обязательна"})
		}

		if cfg.TelegramBotToken == "" || cfg.TelegramAdminID <= 0 {
			return c.Status(400).JSON(fiber.Map{"detail": "Telegram интеграция не настроена"})
		}

		// Валидация подписи Telegram WebApp
		values, err := url.ParseQuery(body.InitData)
		if err != nil {
			return c.Status(400).JSON(fiber.Map{"detail": "Некорректный init_data"})
		}

		receivedHash := values.Get("hash")
		if receivedHash == "" {
			return c.Status(401).JSON(fiber.Map{"detail": "Отсутствует hash подписи"})
		}
		values.Del("hash")

		keys := make([]string, 0, len(values))
		for k := range values {
			keys = append(keys, k)
		}
		sort.Strings(keys)

		var sb strings.Builder
		for i, k := range keys {
			if i > 0 {
				sb.WriteString("\n")
			}
			sb.WriteString(k)
			sb.WriteString("=")
			sb.WriteString(values.Get(k))
		}

		secretHmac := hmac.New(sha256.New, []byte("WebAppData"))
		secretHmac.Write([]byte(cfg.TelegramBotToken))
		secretKey := secretHmac.Sum(nil)

		dataHmac := hmac.New(sha256.New, secretKey)
		dataHmac.Write([]byte(sb.String()))
		computedHash := hex.EncodeToString(dataHmac.Sum(nil))

		if !hmac.Equal([]byte(receivedHash), []byte(computedHash)) {
			return c.Status(401).JSON(fiber.Map{"detail": "Недействительная подпись Telegram"})
		}

		// SEC-01: после проверки подписи контролируем свежесть auth_date,
		// чтобы перехваченный init_data нельзя было переиспользовать (replay-атака).
		authDateRaw := values.Get("auth_date")
		if authDateRaw == "" {
			return c.Status(401).JSON(fiber.Map{"detail": "Отсутствует поле auth_date"})
		}
		authDateUnix, err := strconv.ParseInt(authDateRaw, 10, 64)
		if err != nil || authDateUnix <= 0 {
			return c.Status(401).JSON(fiber.Map{"detail": "Некорректное значение auth_date"})
		}
		nowUnix := time.Now().Unix()
		if authDateUnix > nowUnix+int64(telegramInitDataClockSkew.Seconds()) {
			return c.Status(401).JSON(fiber.Map{"detail": "auth_date находится в будущем"})
		}
		if nowUnix-authDateUnix > int64(telegramInitDataMaxAgeSeconds) {
			return c.Status(401).JSON(fiber.Map{"detail": "Срок действия init_data истёк (старше 24 часов)"})
		}

		var userMap struct {
			ID int64 `json:"id"`
		}
		if err := json.Unmarshal([]byte(values.Get("user")), &userMap); err != nil {
			return c.Status(401).JSON(fiber.Map{"detail": "Некорректные данные пользователя в init_data"})
		}

		if userMap.ID != cfg.TelegramAdminID {
			return c.Status(403).JSON(fiber.Map{"detail": "Доступ запрещён для данного Telegram аккаунта"})
		}

		userData := UserSession{
			Username:   "tg_admin",
			Role:       "SUPERADMIN",
			TelegramID: userMap.ID,
			Bootstrap:  true,
		}

		if appRedis.Client != nil {
			sessionBytes := make([]byte, 16)
			if _, err := rand.Read(sessionBytes); err == nil {
				sessionID := hex.EncodeToString(sessionBytes)
				sessionPayload := map[string]any{
					"user":       userData,
					"created_at": time.Now().UTC().Format(time.RFC3339),
				}
				if sessionData, err := json.Marshal(sessionPayload); err == nil {
					if err := appRedis.Client.Set(c.Context(), "session:"+sessionID, string(sessionData), 7*24*time.Hour).Err(); err != nil {
						log.Printf("Не удалось сохранить сессию Telegram WebApp в Redis: %v", err)
					} else if cfg != nil && cfg.SessionSecretKey != "" {
						signedCookie := signItsdangerous(sessionID, cfg.SessionSecretKey)
						isSecure := c.Protocol() == "https" || c.Get("X-Forwarded-Proto") == "https"
						c.Cookie(&fiber.Cookie{
							Name:     "session",
							Value:    signedCookie,
							Path:     "/",
							HTTPOnly: true,
							SameSite: "Lax",
							MaxAge:   7 * 24 * 3600,
							Secure:   isSecure,
						})
					}
				}
			}
		}

		return c.JSON(fiber.Map{
			"success":    true,
			"user":       userData,
			"csrf_token": currentCSRFToken(c),
		})
	}
}

func Login(c *fiber.Ctx) error {
	var body struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := c.BodyParser(&body); err != nil {
		return c.Status(400).JSON(fiber.Map{"detail": "Неверный формат запроса"})
	}

	if !ensurePool(c, "аутентификация пользователя") {
		return nil
	}

	ctx := c.Context()
	var userID int64
	var role, hash string
	var deptID *int64
	var isActive bool

	query := `SELECT id, role, password_hash, department_id, is_active FROM web_users WHERE username = $1`
	err := database.Pool.QueryRow(ctx, query, body.Username).Scan(&userID, &role, &hash, &deptID, &isActive)
	if err != nil {
		// SEC-03: пользователь не найден — выполняем фиктивное сравнение
		// пароля с постоянным хешем, чтобы время отклика не раскрывало
		// существование логина (timing attack / перечисление логинов).
		dummyPasswordCompare(body.Password)
		return c.Status(401).JSON(fiber.Map{"detail": "Неверный логин или пароль"})
	}

	if err := bcrypt.CompareHashAndPassword([]byte(hash), []byte(body.Password)); err != nil || !isActive {
		return c.Status(401).JSON(fiber.Map{"detail": "Неверный логин или пароль"})
	}

	user := UserSession{
		Username:     body.Username,
		Role:         role,
		WebUserID:    &userID,
		DepartmentID: deptID,
	}

	return c.JSON(fiber.Map{
		"success":    true,
		"needs_2fa":  false,
		"user":       user,
		"csrf_token": currentCSRFToken(c),
	})
}
