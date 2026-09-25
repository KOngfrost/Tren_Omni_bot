package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"student_bot/backend_go/internal/config"
	"student_bot/backend_go/internal/database"
	"student_bot/backend_go/internal/handlers"
	"student_bot/backend_go/internal/metrics"
	appRedis "student_bot/backend_go/internal/redis"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/compress"
	"github.com/gofiber/fiber/v2/middleware/cors"
	"github.com/gofiber/fiber/v2/middleware/logger"
	"github.com/gofiber/fiber/v2/middleware/recover"
)

func main() {
	cfg := config.LoadConfig()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// Подключение к БД и Redis: критичные зависимости — только fail-fast.
	// Тихий лог ошибки оставлял контейнер «полуживым» с database.Pool == nil,
	// что приводило к паникам/ложным ответам обработчиков. Теперь процесс
	// завершается с ошибкой, и оркестратор перезапускает контейнер
	// (в docker-compose.yml для go-api задан restart: unless-stopped).
	if _, err := database.ConnectDB(ctx, cfg.DatabaseURL); err != nil {
		log.Fatalf("Критическая ошибка подключения к базе данных: %v", err)
	}

	if _, err := appRedis.ConnectRedis(ctx, cfg.RedisURL); err != nil {
		log.Fatalf("Критическая ошибка подключения к Redis: %v", err)
	}

	// Инициализация Fiber приложения с оптимизацией
	app := fiber.New(fiber.Config{
		AppName:               "OSS Bot High-Performance Go Fiber Gateway",
		ServerHeader:          "GoFiber",
		StrictRouting:         false,
		CaseSensitive:         false,
		BodyLimit:             15 * 1024 * 1024, // 15MB
		ReadTimeout:           10 * time.Second,
		WriteTimeout:          10 * time.Second,
		DisableStartupMessage: false,
	})

	// Middleware
	app.Use(recover.New())
	app.Use(compress.New(compress.Config{
		Level: compress.LevelBestSpeed,
	}))
	app.Use(logger.New(logger.Config{
		Format: "[${time}] ${status} - ${latency} ${method} ${path}\n",
	}))
	app.Use(cors.New(cors.Config{
		AllowOrigins:     cfg.CORSOrigins,
		AllowCredentials: true,
		AllowHeaders:     "Origin, Content-Type, Accept, X-CSRF-Token",
	}))
	// Prometheus (этап 5.3). Регистрируется после recover, чтобы паники,
	// перехваченные recover, тоже попадали в метрики со статусом 500.
	app.Use(metrics.Middleware())

	// Эндпоинт метрик вне группы /api/v1: не проходит через CSRFProtect
	// и не влияет на бизнес-логику. Prometheus опрашивает его по
	// внутренней docker-сети (healthcheck в docker-compose.yml).
	metrics.RegisterMetricsRoute(app, "/metrics")

	// Маршруты API v1
	api := app.Group("/api/v1")
	// SEC-02: проверка X-CSRF-Token на всех POST/PUT/DELETE/PATCH
	// эндпоинтах (double-submit cookie), генерация токена на безопасных методах.
	api.Use(handlers.CSRFProtect())
	api.Get("/health", handlers.HealthCheck)
	api.Get("/stats", handlers.GetStats(cfg))

	// Отделы
	api.Get("/departments/", handlers.GetDepartments)
	api.Post("/departments/", handlers.CreateDepartment)

	// Тикеты
	api.Get("/tickets/", handlers.GetTickets)

	// Аутентификация
	api.Get("/auth/me", handlers.AuthMe)
	api.Post("/auth/login", handlers.Login)
	api.Post("/auth/telegram-webapp", handlers.TelegramWebAppAuth(cfg))
	api.Post("/auth/logout", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"success": true})
	})

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		log.Println("Получен сигнал завершения работы, мягкая остановка Go Fiber...")
		_ = app.ShutdownWithTimeout(5 * time.Second)
	}()

	log.Printf("🚀 Высокоскоростной Go Fiber сервер запущен на порту %s", cfg.Port)
	if err := app.Listen(":" + cfg.Port); err != nil {
		log.Fatalf("Ошибка работы сервера: %v", err)
	}
}
