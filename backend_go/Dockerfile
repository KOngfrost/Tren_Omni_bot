# Этап сборки (Builder)
FROM golang:1.23-alpine AS builder

WORKDIR /build

RUN apk add --no-cache git ca-certificates

COPY go.mod ./
# Скачиваем зависимости
RUN go mod download || true

COPY . .

# Сборка статического бинарника без CGO с оптимизацией размера
RUN go mod tidy && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o /build/go-server ./cmd/server

# Этап выполнения (Final Minimal Image)
FROM alpine:3.20

RUN apk --no-cache add ca-certificates tzdata \
    && addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

COPY --from=builder /build/go-server /app/go-server

USER appuser

EXPOSE 8080

CMD ["/app/go-server"]
