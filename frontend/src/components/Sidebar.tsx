import React from 'react'
import { LayoutDashboard, Ticket, Building2, Shield, LogOut, Sun, Moon } from 'lucide-react'
import type { User } from '../types'

interface SidebarProps {
  currentView: string
  setCurrentView: (view: string) => void
  user: User
  onLogout: () => void
  theme: string
  toggleTheme: () => void
  isOpen: boolean
  setIsOpen: (open: boolean) => void
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentView,
  setCurrentView,
  user,
  onLogout,
  theme,
  toggleTheme,
  isOpen,
  setIsOpen,
}) => {
  const isSuperadmin = user.role.toUpperCase() === 'SUPERADMIN'

  return (
    <>
      {isOpen && (
        <div
          onClick={() => setIsOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.5)',
            zIndex: 90,
          }}
        />
      )}
      <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 28 }}>
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 800,
              color: '#fff',
            }}
          >
            OB
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>OSS Bot</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Панель управления</div>
          </div>
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
          <button
            onClick={() => {
              setCurrentView('dashboard')
              setIsOpen(false)
            }}
            className={`nav-link ${currentView === 'dashboard' ? 'active' : ''}`}
            style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%' }}
          >
            <LayoutDashboard size={18} />
            Рабочий стол
          </button>

          <button
            onClick={() => {
              setCurrentView('tickets')
              setIsOpen(false)
            }}
            className={`nav-link ${currentView === 'tickets' ? 'active' : ''}`}
            style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%' }}
          >
            <Ticket size={18} />
            Заявки и тикеты
          </button>

          <button
            onClick={() => {
              setCurrentView('departments')
              setIsOpen(false)
            }}
            className={`nav-link ${currentView === 'departments' ? 'active' : ''}`}
            style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%' }}
          >
            <Building2 size={18} />
            Отделы
          </button>

          {isSuperadmin && (
            <button
              onClick={() => {
                setCurrentView('security')
                setIsOpen(false)
              }}
              className={`nav-link ${currentView === 'security' ? 'active' : ''}`}
              style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%' }}
            >
              <Shield size={18} />
              Безопасность & Аудит
            </button>
          )}
        </nav>

        {/* User Card */}
        <div style={{ marginTop: 'auto', paddingTop: 16, borderTop: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <div
              style={{
                width: 34,
                height: 34,
                borderRadius: '50%',
                background: 'rgba(255,255,255,0.1)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 600,
                fontSize: 14,
              }}
            >
              {user.username.slice(0, 2).toUpperCase()}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap' }}>
                {user.username}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                {isSuperadmin ? 'Суперадмин' : 'Админ отдела'}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={toggleTheme}
              className="btn btn-secondary"
              style={{ flex: 1, padding: 8 }}
              title="Сменить тему оформления"
            >
              {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <button
              onClick={onLogout}
              className="btn btn-secondary"
              style={{ flex: 1, padding: 8 }}
              title="Выйти из системы"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}
