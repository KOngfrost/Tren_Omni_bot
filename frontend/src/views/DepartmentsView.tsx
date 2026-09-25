import React, { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { Department } from '../types'
import { Building2, Plus } from 'lucide-react'

export const DepartmentsView: React.FC = () => {
  const [departments, setDepartments] = useState<Department[]>([])
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [newDeptName, setNewDeptName] = useState('')
  const [error, setError] = useState('')

  const loadDepartments = async () => {
    setLoading(true)
    try {
      const data = await api.getDepartments()
      setDepartments(data)
    } catch (err: any) {
      console.error('Ошибка загрузки отделов:', err)
      setError(err.message || 'Не удалось загрузить отделы')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadDepartments()
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newDeptName.trim()) return

    try {
      await api.createDepartment(newDeptName.trim())
      setNewDeptName('')
      setModalOpen(false)
      loadDepartments()
    } catch (err: any) {
      setError(err.message || 'Ошибка создания отдела')
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 26, fontWeight: 800, marginBottom: 4 }}>Отделы</h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
            Управление структурами и распределение потока обращений
          </p>
        </div>
        <button onClick={() => setModalOpen(true)} className="btn btn-primary">
          <Plus size={16} />
          Создать отдел
        </button>
      </div>

      {error && (
        <div style={{ padding: '12px 16px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid var(--danger)', borderRadius: 8, color: 'var(--danger)', fontSize: 14 }}>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>Загрузка отделов...</div>
      ) : departments.length === 0 ? (
        <div className="glass-panel" style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>
          Отделы не найдены. Создайте первый отдел.
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
          {departments.map((d) => (
            <div key={d.id} className="glass-panel" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 8,
                    background: 'rgba(59, 130, 246, 0.15)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--accent)',
                  }}
                >
                  <Building2 size={20} />
                </div>
                <div style={{ fontWeight: 700, fontSize: 16 }}>{d.name}</div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 8, paddingTop: 12, borderTop: '1px solid var(--border-color)', fontSize: 13, color: 'var(--text-secondary)' }}>
                <div>Тикетов: <strong style={{ color: 'var(--text-primary)' }}>{d.usage?.tickets ?? 0}</strong></div>
                <div>База знаний: <strong style={{ color: 'var(--text-primary)' }}>{d.usage?.knowledge ?? 0}</strong></div>
                <div>FAQ статей: <strong style={{ color: 'var(--text-primary)' }}>{d.usage?.faq ?? 0}</strong></div>
                <div>Админов: <strong style={{ color: 'var(--text-primary)' }}>{d.usage?.admins ?? 0}</strong></div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modal Dialog */}
      {modalOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: 16,
          }}
        >
          <div className="glass-panel" style={{ width: '100%', maxWidth: 420, padding: 24, background: 'var(--bg-secondary)' }}>
            <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 12 }}>Создать новый отдел</h3>
            <form onSubmit={handleCreate} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
                  Название отдела
                </label>
                <input
                  type="text"
                  className="input"
                  placeholder="Например: Учебная часть"
                  value={newDeptName}
                  onChange={(e) => setNewDeptName(e.target.value)}
                  autoFocus
                  required
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                <button type="button" onClick={() => setModalOpen(false)} className="btn btn-secondary">
                  Отмена
                </button>
                <button type="submit" className="btn btn-primary">
                  Создать
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
