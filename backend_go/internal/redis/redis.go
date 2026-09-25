package redis

import (
	"context"
	"log"

	"github.com/redis/go-redis/v9"
)

var Client *redis.Client

func ConnectRedis(ctx context.Context, redisURL string) (*redis.Client, error) {
	opt, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, err
	}

	client := redis.NewClient(opt)
	if err := client.Ping(ctx).Err(); err != nil {
		log.Printf("Предупреждение: Redis не ответил на ping: %v", err)
	} else {
		log.Println("Успешное подключение к Redis")
	}

	Client = client
	return client, nil
}
