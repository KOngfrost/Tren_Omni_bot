package redis

import (
	"context"
	"fmt"
	"log"

	"github.com/redis/go-redis/v9"
)

var Client *redis.Client

// ConnectRedis создаёт клиент и проверяет доступность Redis.
// При недоступном Redis возвращается ошибка: вызывающий код обязан завершить
// работу (fail-fast), чтобы контейнер перезапустился оркестратором, а не
// обслуживал запросы с нерабочим кэшем и сессиями.
func ConnectRedis(ctx context.Context, redisURL string) (*redis.Client, error) {
	opt, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("ошибка парсинга redis url: %w", err)
	}

	client := redis.NewClient(opt)
	if err := client.Ping(ctx).Err(); err != nil {
		if closeErr := client.Close(); closeErr != nil {
			log.Printf("Ошибка закрытия соединения с Redis: %v", closeErr)
		}
		return nil, fmt.Errorf("redis недоступен: %w", err)
	}

	log.Println("Успешное подключение к Redis")
	Client = client
	return client, nil
}
