import React, { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { TicketSummary } from '../types'
import { Search, RefreshCw } from 'lucide-react'

export const TicketsView: React.FC = () => {
  const [tickets, setTickets] = useState<TicketSummary[]>([])
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [loading, setLoading] = useState(true)

  const loadTickets = async () => {
    setLoading(true)
    try {
      const data = await api.getTickets(100)
      setTickets(data)
    } catch (err) {
      console.error('Ошибка загрузки тикетов:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTickets()
  }, [])

  const filteredTickets = tickets.filter((t) => {
    const matchesSearch =
      t.topic.toLowerCase().includes(search.toLowerCase()) ||
      (t.department_name && t.department_name.toLowerCase().includes(search.toLowerCase())) ||
      t.id.toString().includes(search)

    if (statusFilter === 'all') return matchesSearch
    return matchesSearch && t.status === statusFilter
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 26, fontWeight: 800, marginBottom: 4 }}>Заявки и обращения</h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
            Центр обработки вопросов студентов, распределение по отделам
          </p>
        </div>
        <button onClick={loadTickets} className="btn btn-secondary">
          <RefreshCw size={16} />
          Обновить
        </button>
      </div>

      {/* Filter and Search Bar */}
      <div className="glass-panel" style={{ padding: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <Search size={16} style={{ position: 'absolute', left: 12, top: 12, color: 'var(--text-secondary)' }} />
          <input
            type="text"
            className="input"
            placeholder="Поиск по теме, номеру # или отделу..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 38 }}
          />
        </div>
        <select
          className="input"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          style={{ width: 'auto', minWidth: 160 }}
        >
          <option value="all">Все статусы</option>
          <option value="new">Новые</option>
          <option value="in_progress">В работе</option>
          <option value="resolved">Решённые</option>
          <option value="closed">Закрытые</option>
        </select>
      </div>

      {/* Tickets List */}
      <div className="glass-panel" style={{ overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>Загрузка списка заявок...</div>
        ) : filteredTickets.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>Заявки не найдены.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)', background: 'rgba(0,0,0,0.1)' }}>
                  <th style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>ID</th>
                  <th style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>Тема</th>
                  <th style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>Отдел</th>
                  <th style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>Дата</th>
                  <th style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>Статус</th>
                </tr>
              </thead>
              <tbody>
                {filteredTickets.map((t) => (
                  <tr
                    key={t.id}
                    style={{ borderBottom: '1px solid var(--border-color)', transition: 'background 0.15s' }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={{ padding: '14px 16px', fontWeight: 700, color: 'var(--accent)' }}>#{t.id}</td>
                    <td style={{ padding: '14px 16px', fontWeight: 600 }}>{t.topic || 'Без темы'}</td>
                    <td style={{ padding: '14px 16px', color: 'var(--text-secondary)', fontSize: 14 }}>
                      {t.department_name || '—'}
                    </td>
                    <td style={{ padding: '14px 16px', color: 'var(--text-secondary)', fontSize: 13 }}>
                      {t.created_at ? new Date(t.created_at).toLocaleDateString('ru-RU') : '—'}
                    </td>
                    <td style={{ padding: '14px 16px' }}>
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
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
