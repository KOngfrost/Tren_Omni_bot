package handlers

import (
	"crypto/hmac"
	"crypto/rand"
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
	c.Cookie(&fiber.Cookie{
		Name:     csrfCookieName,
		Value:    token,
		Path:     "/",
		MaxAge:   csrfCookieMaxAgeSeconds,
		SameSite: "Lax",
		HTTPOnly: false,
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

// === Handlers ===

func HealthCheck(c *fiber.Ctx) error {
	return c.JSON(fiber.Map{
		"status":    "healthy",
		"framework": "Fiber v2 (Go)",
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	})
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
		if err := database.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM tickets WHERE status IN ('new', 'in_progress')").Scan(&activeTickets); err != nil {
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
	ctx := c.Context()

	if !ensurePool(c, "список отделов") {
		return nil
	}

	rows, err := database.Pool.Query(ctx, "SELECT id, name FROM departments ORDER BY name ASC")
	if err != nil {
		return respondDatabaseError(c, "выборка отделов", err)
	}
	defer rows.Close()

	depts := make([]DepartmentItem, 0)
	for rows.Next() {
		var d DepartmentItem
		if err := rows.Scan(&d.ID, &d.Name); err != nil {
			return respondDatabaseError(c, "чтение списка отделов", err)
		}

		var ticketCount int
		if err := database.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM tickets WHERE department_id = $1", d.ID).Scan(&ticketCount); err != nil {
			return respondDatabaseError(c, "подсчёт тикетов отдела", err)
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
	var body struct {
		Name string `json:"name"`
	}
	if err := c.BodyParser(&body); err != nil || strings.TrimSpace(body.Name) == "" {
		return c.Status(400).JSON(fiber.Map{"detail": "Название отдела не может быть пустым"})
	}

	name := strings.TrimSpace(body.Name)
	ctx := c.Context()

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
	ctx := c.Context()
	limit := c.QueryInt("limit", 50)
	if limit < 1 || limit > 100 {
		limit = 50
	}

	if !ensurePool(c, "список тикетов") {
		return nil
	}

	query := `
		SELECT t.id, COALESCE(t.topic, ''), COALESCE(t.status, 'new'), t.department_id, d.name, t.created_at
		FROM tickets t
		LEFT JOIN departments d ON t.department_id = d.id
		ORDER BY t.created_at DESC
		LIMIT $1
	`
	rows, err := database.Pool.Query(ctx, query, limit)
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

// === Auth Handlers ===

func AuthMe(c *fiber.Ctx) error {
	sessionID := c.Cookies("session")
	if sessionID == "" || appRedis.Client == nil {
		return c.JSON(fiber.Map{"authenticated": false, "user": nil, "csrf_token": currentCSRFToken(c)})
	}

	val, err := appRedis.Client.Get(c.Context(), "session:"+sessionID).Result()
	if err != nil {
		return c.JSON(fiber.Map{"authenticated": false, "user": nil, "csrf_token": currentCSRFToken(c)})
	}

	var sessionData map[string]interface{}
	_ = json.Unmarshal([]byte(val), &sessionData)
	user, exists := sessionData["user"]

	return c.JSON(fiber.Map{
		"authenticated": exists && user != nil,
		"user":          user,
		"csrf_token":    currentCSRFToken(c),
	})
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
