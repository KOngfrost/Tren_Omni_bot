package database

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

var Pool *pgxpool.Pool

const (
	// Число попыток первичного подключения к БД и пауза между ними.
	dbConnectAttempts   = 3
	dbConnectRetryDelay = time.Second
)

func ConnectDB(ctx context.Context, connString string) (*pgxpool.Pool, error) {
	config, err := pgxpool.ParseConfig(connString)
	if err != nil {
		return nil, fmt.Errorf("ошибка парсинга database url: %w", err)
	}

	config.MaxConns = 25
	config.MinConns = 5
	config.MaxConnLifetime = 30 * time.Minute
	config.MaxConnIdleTime = 5 * time.Minute

	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, fmt.Errorf("ошибка подключения к postgresql pool: %w", err)
	}

	// B1: если БД недоступна, возвращаем ошибку (а не «полуживой» Pool == nil),
	// чтобы вызывающий код завершил процесс и оркестратор перезапустил контейнер.
	var pingErr error
	for attempt := 1; attempt <= dbConnectAttempts; attempt++ {
		pingErr = pool.Ping(ctx)
		if pingErr == nil {
			break
		}
		log.Printf("Попытка %d/%d подключения к PostgreSQL не удалась: %v", attempt, dbConnectAttempts, pingErr)
		if attempt < dbConnectAttempts && ctx.Err() == nil {
			select {
			case <-ctx.Done():
			case <-time.After(dbConnectRetryDelay):
			}
		}
	}

	if pingErr != nil {
		pool.Close()
		return nil, fmt.Errorf("база данных недоступна после %d попыток: %w", dbConnectAttempts, pingErr)
	}

	log.Println("Успешное подключение к PostgreSQL через pgxpool")
	Pool = pool
	return pool, nil
}
