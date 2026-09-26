package config

import (
	"os"
	"strconv"
)

type Config struct {
	Port              string
	DatabaseURL       string
	RedisURL          string
	SessionSecretKey  string
	TelegramBotToken  string
	TelegramAdminID   int64
	TwoFactorEnabled  bool
	CORSOrigins       string
	AppVersion        string
}

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func LoadConfig() *Config {
	port := getEnv("PORT", "8080")
	dbHost := getEnv("DB_HOST", "pgbouncer")
	dbPort := getEnv("DB_PORT", "6432")
	dbUser := getEnv("POSTGRES_USER", "oss_bot")
	dbPass := getEnv("POSTGRES_PASSWORD", "")
	dbName := getEnv("POSTGRES_DB", "oss_bot")

	dbURL := getEnv("DATABASE_URL", "postgres://"+dbUser+":"+dbPass+"@"+dbHost+":"+dbPort+"/"+dbName+"?sslmode=disable")
	redisURL := getEnv("REDIS_URL", "redis://oss_bot_redis:6379/0")

	tgAdminID, _ := strconv.ParseInt(getEnv("TELEGRAM_ADMIN_ID", "0"), 10, 64)
	twoFactorEnabled, _ := strconv.ParseBool(getEnv("TWO_FACTOR_ENABLED", "true"))
	corsOrigins := getEnv("CORS_ALLOWED_ORIGINS", "http://localhost:3000, http://127.0.0.1:3000, http://localhost:8000")

	return &Config{
		Port:             port,
		DatabaseURL:      dbURL,
		RedisURL:         redisURL,
		SessionSecretKey: getEnv("SESSION_SECRET_KEY", "change_me_super_secret_key_32_chars"),
		TelegramBotToken: getEnv("TELEGRAM_BOT_TOKEN", ""),
		TelegramAdminID:  tgAdminID,
		TwoFactorEnabled: twoFactorEnabled,
		CORSOrigins:      corsOrigins,
		AppVersion:       getEnv("APP_VERSION", "0.8.8.2"),
	}
}
