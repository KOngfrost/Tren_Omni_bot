import React, { useEffect, useState } from 'react'
import { api } from '../services/api'
import { ShieldCheck, Lock, Activity, Terminal } from 'lucide-react'

export const SecurityView: React.FC = () => {
  const [stats, setStats] = useState<any>(null)

  useEffect(() => {
    async function load() {
      try {
        const s = await api.getStats()
        setStats(s)
      } catch (err) {
        console.error(err)
      }
    }
    load()
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h1 style={{ fontSize: 26, fontWeight: 800, marginBottom: 4 }}>Безопасность и Сеть</h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
          Мониторинг защищенности системы, сессий, токенов и аудит периметра
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
        <div className="glass-panel" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <ShieldCheck size={20} color="var(--success)" />
            <h3 style={{ fontSize: 16, fontWeight: 700 }}>Двухфакторная защита (2FA)</h3>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Защита от подбора паролей активна. Вход разрешается только после валидации одноразового OTP через доверенный VK-канал.
          </p>
        </div>

        <div className="glass-panel" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <Lock size={20} color="var(--accent)" />
            <h3 style={{ fontSize: 16, fontWeight: 700 }}>Защита сессий и Cookie</h3>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Флаги HttpOnly, SameSite=Lax, Secure активны. Реализована автоматическая ротация ID сессии при входе (защита от Session Fixation).
          </p>
        </div>

        <div className="glass-panel" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <Activity size={20} color="var(--warning)" />
            <h3 style={{ fontSize: 16, fontWeight: 700 }}>Rate Limiting & DDoS</h3>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Базовый лимит 5 попыток за 15 мин на IP в PostgreSQL. Защита от Slowloris и лимит 10 МБ на тела запросов.
          </p>
        </div>
      </div>

      <div className="glass-panel" style={{ padding: 24 }}>
        <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Terminal size={18} />
          Параметры окружения
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, fontSize: 13 }}>
          <div>Окружение: <strong>{stats?.environment || 'production'}</strong></div>
          <div>Версия ядра: <strong>{stats?.version || '0.8.4.1'}</strong></div>
          <div>Redis распределенный кэш: <strong>{stats?.redis_connected ? 'Да' : 'Нет'}</strong></div>
          <div>Sentry мониторинг: <strong>{stats?.sentry_enabled ? 'Активен' : 'Не задан'}</strong></div>
        </div>
      </div>
    </div>
  )
}
