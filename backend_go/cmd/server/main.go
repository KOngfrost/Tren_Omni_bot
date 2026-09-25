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

	// Подключение к БД и Redis
	_, err := database.ConnectDB(ctx, cfg.DatabaseURL)
	if err != nil {
		log.Printf("Предупреждение подключения к БД: %v", err)
	}

	_, _ = appRedis.ConnectRedis(ctx, cfg.RedisURL)

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
		AllowOrigins:     "http://localhost:3000, http://127.0.0.1:3000, http://localhost:8000",
		AllowCredentials: true,
		AllowHeaders:     "Origin, Content-Type, Accept, X-CSRF-Token",
	}))

	// Маршруты API v1
	api := app.Group("/api/v1")
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
