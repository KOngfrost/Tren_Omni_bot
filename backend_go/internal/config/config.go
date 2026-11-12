package config

import (
	"crypto/rand"
	"encoding/base64"
	"log"
	"net"
	"net/url"
	"os"
	"strconv"
)

type Config struct {
	Port              string
	DatabaseURL       string
	RedisURL          string
	SessionSecretKey  string
	ServiceAPIKey     string
	TelegramBotToken  string
	TelegramAdminID   int64
	TwoFactorEnabled  bool
	CORSOrigins       string
	AppVersion        string
	AppEnv            string
}

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func generateDevSecret() string {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		log.Fatal("CRITICAL: не удалось сгенерировать случайный dev-секрет")
	}
	return base64.RawURLEncoding.EncodeToString(buf)
}

func LoadConfig() *Config {
	port := getEnv("PORT", "8080")
	dbHost := getEnv("DB_HOST", "pgbouncer")
	dbPort := getEnv("DB_PORT", "6432")
	dbUser := getEnv("POSTGRES_USER", "oss_bot")
	dbPass := getEnv("POSTGRES_PASSWORD", "")
	dbName := getEnv("POSTGRES_DB", "oss_bot")

	defaultDBURL := (&url.URL{
		Scheme:   "postgres",
		User:     url.UserPassword(dbUser, dbPass),
		Host:     net.JoinHostPort(dbHost, dbPort),
		Path:     "/" + dbName,
		RawQuery: "sslmode=disable",
	}).String()
	dbURL := getEnv("DATABASE_URL", defaultDBURL)
	redisURL := getEnv("REDIS_URL", "redis://oss_bot_redis:6379/0")

	tgAdminID, _ := strconv.ParseInt(getEnv("TELEGRAM_ADMIN_ID", "0"), 10, 64)
	twoFactorEnabled, _ := strconv.ParseBool(getEnv("TWO_FACTOR_ENABLED", "true"))
	corsOrigins := getEnv("CORS_ALLOWED_ORIGINS", "http://localhost:3000, http://127.0.0.1:3000, http://localhost:8000")
	appEnv := getEnv("APP_ENV", "development")

	sessionSecret := getEnv("SESSION_SECRET_KEY", "")
	if sessionSecret == "" {
		sessionSecret = getEnv("SECRET_KEY", "")
	}
	if appEnv == "production" {
		if sessionSecret == "" || sessionSecret == "change_me_super_secret_key_32_chars" || len(sessionSecret) < 32 {
			log.Fatal("CRITICAL: SESSION_SECRET_KEY (или SECRET_KEY) не задан, является плейсхолдером или короче 32 символов в production")
		}
	} else if sessionSecret == "" {
		sessionSecret = generateDevSecret()
	}

	serviceAPIKey := getEnv("SERVICE_API_KEY", "")

	return &Config{
		Port:             port,
		DatabaseURL:      dbURL,
		RedisURL:         redisURL,
		SessionSecretKey: sessionSecret,
		ServiceAPIKey:    serviceAPIKey,
		TelegramBotToken: getEnv("TELEGRAM_BOT_TOKEN", ""),
		TelegramAdminID:  tgAdminID,
		TwoFactorEnabled: twoFactorEnabled,
		CORSOrigins:      corsOrigins,
		AppVersion:       getEnv("APP_VERSION", "1.0.0"),
		AppEnv:           appEnv,
	}
}
