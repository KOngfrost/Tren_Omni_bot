import React, { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { SystemStats, TicketSummary } from '../types'
import { Clock, ShieldCheck, Database, Ticket } from 'lucide-react'

export const DashboardView: React.FC = () => {
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [recentTickets, setRecentTickets] = useState<TicketSummary[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function loadData() {
      try {
        const [statsData, ticketsData] = await Promise.all([
          api.getStats(),
          api.getTickets(6),
        ])
        setStats(statsData)
        setRecentTickets(ticketsData)
      } catch (err) {
        console.error('Ошибка загрузки дашборда:', err)
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [])

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>Загрузка метрик системы...</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div>
        <h1 style={{ fontSize: 26, fontWeight: 800, marginBottom: 6 }}>Рабочий стол</h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
          Оперативный статус системы обработки обращений студентов и инфраструктуры
        </p>
      </div>

      {/* Stats Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
        <div className="glass-panel" style={{ padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Всего обращений</span>
            <Ticket size={20} color="var(--accent)" />
          </div>
          <div style={{ fontSize: 28, fontWeight: 800 }}>{stats?.total_tickets ?? 0}</div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
            Накоплено за весь период
          </div>
        </div>

        <div className="glass-panel" style={{ padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>В работе (активные)</span>
            <Clock size={20} color="var(--warning)" />
          </div>
          <div style={{ fontSize: 28, fontWeight: 800, color: 'var(--warning)' }}>
            {stats?.active_tickets ?? 0}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
            Требуют ответа администратора
          </div>
        </div>

        <div className="glass-panel" style={{ padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Статус Redis</span>
            <Database size={20} color={stats?.redis_connected ? 'var(--success)' : 'var(--danger)'} />
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: stats?.redis_connected ? 'var(--success)' : 'var(--danger)' }}>
            {stats?.redis_connected ? 'Подключен' : 'Недоступен'}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
            Распределённые сессии и кэш
          </div>
        </div>

        <div className="glass-panel" style={{ padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Защита 2FA</span>
            <ShieldCheck size={20} color="var(--success)" />
          </div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {stats?.two_factor_enabled ? 'Активна' : 'Отключена'}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
            Одноразовые коды через VK
          </div>
        </div>
      </div>

      {/* Recent Tickets Section */}
      <div className="glass-panel" style={{ padding: 24 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 16 }}>Свежие обращения</h2>
        {recentTickets.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>Обращений пока нет.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {recentTickets.map((t) => (
              <div
                key={t.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '12px 16px',
                  background: 'rgba(0,0,0,0.15)',
                  borderRadius: 8,
                }}
              >
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>
                    #{t.id} — {t.topic || 'Без темы'}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    Отдел: {t.department_name || 'Не назначен'} • {t.created_at ? new Date(t.created_at).toLocaleString('ru-RU') : ''}
                  </div>
                </div>
                <div>
                  <span
                    className={`badge ${
                      t.status === 'new'
                        ? 'badge-warning'
                        : t.status === 'in_progress'
                        ? 'badge-warning'
                        : t.status === 'resolved'
                        ? 'badge-success'
                        : 'badge-secondary'
                    }`}
                  >
                    {t.status === 'new'
                      ? 'Новый'
                      : t.status === 'in_progress'
                      ? 'В работе'
                      : t.status === 'resolved'
                      ? 'Решён'
                      : t.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
