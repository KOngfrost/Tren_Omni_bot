package handlers

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"sort"
	"strings"
	"time"

	"student_bot/backend_go/internal/config"
	"student_bot/backend_go/internal/database"
	appRedis "student_bot/backend_go/internal/redis"

	"github.com/gofiber/fiber/v2"
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
		var totalTickets, activeTickets int

		_ = database.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM tickets").Scan(&totalTickets)
		_ = database.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM tickets WHERE status IN ('new', 'in_progress')").Scan(&activeTickets)

		redisOK := false
		if appRedis.Client != nil {
			redisOK = appRedis.Client.Ping(ctx).Err() == nil
		}

		return c.JSON(fiber.Map{
			"version":             "0.8.4.1",
			"environment":         "production",
			"redis_connected":     redisOK,
			"sentry_enabled":      false,
			"two_factor_enabled":  cfg.TwoFactorEnabled,
			"total_tickets":       totalTickets,
			"active_tickets":      activeTickets,
		})
	}
}

func GetDepartments(c *fiber.Ctx) error {
	ctx := c.Context()
	rows, err := database.Pool.Query(ctx, "SELECT id, name FROM departments ORDER BY name ASC")
	if err != nil {
		return c.Status(500).JSON(fiber.Map{"error": "Ошибка получения отделов"})
	}
	defer rows.Close()

	depts := make([]DepartmentItem, 0)
	for rows.Next() {
		var d DepartmentItem
		if err := rows.Scan(&d.ID, &d.Name); err == nil {
			var ticketCount int
			_ = database.Pool.QueryRow(ctx, "SELECT COUNT(*) FROM tickets WHERE department_id = $1", d.ID).Scan(&ticketCount)
			d.Usage = &DepartmentUsage{Tickets: ticketCount}
			depts = append(depts, d)
		}
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

	var newID int64
	err := database.Pool.QueryRow(ctx, "INSERT INTO departments (name) VALUES ($1) RETURNING id", name).Scan(&newID)
	if err != nil {
		return c.Status(400).JSON(fiber.Map{"detail": "Отдел с таким названием уже существует"})
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

	query := `
		SELECT t.id, COALESCE(t.topic, ''), COALESCE(t.status, 'new'), t.department_id, d.name, t.created_at
		FROM tickets t
		LEFT JOIN departments d ON t.department_id = d.id
		ORDER BY t.created_at DESC
		LIMIT $1
	`
	rows, err := database.Pool.Query(ctx, query, limit)
	if err != nil {
		return c.Status(500).JSON(fiber.Map{"error": "Ошибка выборки тикетов"})
	}
	defer rows.Close()

	tickets := make([]TicketItem, 0)
	for rows.Next() {
		var t TicketItem
		var createdAt *time.Time
		if err := rows.Scan(&t.ID, &t.Topic, &t.Status, &t.DepartmentID, &t.DepartmentName, &createdAt); err == nil {
			if createdAt != nil {
				formatted := createdAt.Format(time.RFC3339)
				t.CreatedAt = &formatted
			}
			tickets = append(tickets, t)
		}
	}

	return c.JSON(tickets)
}

// === Auth Handlers ===

func AuthMe(c *fiber.Ctx) error {
	sessionID := c.Cookies("session")
	if sessionID == "" || appRedis.Client == nil {
		return c.JSON(fiber.Map{"authenticated": false, "user": nil, "csrf_token": "go_csrf_token"})
	}

	val, err := appRedis.Client.Get(c.Context(), "session:"+sessionID).Result()
	if err != nil {
		return c.JSON(fiber.Map{"authenticated": false, "user": nil, "csrf_token": "go_csrf_token"})
	}

	var sessionData map[string]interface{}
	_ = json.Unmarshal([]byte(val), &sessionData)
	user, exists := sessionData["user"]

	return c.JSON(fiber.Map{
		"authenticated": exists && user != nil,
		"user":          user,
		"csrf_token":    "go_csrf_token",
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

		var userMap struct {
			ID int64 `json:"id"`
		}
		_ = json.Unmarshal([]byte(values.Get("user")), &userMap)

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
			"csrf_token": "go_csrf_token",
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

	ctx := c.Context()
	var userID int64
	var role, hash string
	var deptID *int64
	var isActive bool

	query := `SELECT id, role, password_hash, department_id, is_active FROM web_users WHERE username = $1`
	err := database.Pool.QueryRow(ctx, query, body.Username).Scan(&userID, &role, &hash, &deptID, &isActive)
	if err != nil || !isActive {
		return c.Status(401).JSON(fiber.Map{"detail": "Неверный логин или пароль"})
	}

	if err := bcrypt.CompareHashAndPassword([]byte(hash), []byte(body.Password)); err != nil {
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
		"csrf_token": "go_csrf_token",
	})
}
