import React, { useEffect, useState } from 'react'
import { api } from './services/api'
import type { User } from './types'
import { Sidebar } from './components/Sidebar'
import { DashboardView } from './views/DashboardView'
import { TicketsView } from './views/TicketsView'
import { DepartmentsView } from './views/DepartmentsView'
import { SecurityView } from './views/SecurityView'
import { LoginView } from './views/LoginView'
import { Menu } from 'lucide-react'

export const App: React.FC = () => {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [currentView, setCurrentView] = useState('dashboard')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('app_theme') as 'dark' | 'light') || 'dark'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('app_theme', theme)
  }, [theme])

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }

  useEffect(() => {
    async function checkAuth() {
      try {
        const res = await api.getMe()
        if (res.authenticated && res.user) {
          setUser(res.user)
        }
      } catch (err) {
        console.warn('Не авторизован')
      } finally {
        setLoading(false)
      }
    }
    checkAuth()
  }, [])

  const handleLogout = async () => {
    try {
      await api.logout()
    } finally {
      setUser(null)
      setCurrentView('dashboard')
    }
  }

  if (loading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-secondary)',
          fontSize: 16,
        }}
      >
        Загрузка приложения...
      </div>
    )
  }

  if (!user) {
    return <LoginView onSuccess={setUser} />
  }

  return (
    <div className="app-container">
      <Sidebar
        currentView={currentView}
        setCurrentView={setCurrentView}
        user={user}
        onLogout={handleLogout}
        theme={theme}
        toggleTheme={toggleTheme}
        isOpen={sidebarOpen}
        setIsOpen={setSidebarOpen}
      />

      <main className="main-content">
        {/* Mobile top bar */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <button
            onClick={() => setSidebarOpen(true)}
            className="btn btn-secondary"
            style={{ display: 'inline-flex', padding: 8 }}
            aria-label="Открыть меню"
          >
            <Menu size={20} />
          </button>
        </div>

        {currentView === 'dashboard' && <DashboardView />}
        {currentView === 'tickets' && <TicketsView />}
        {currentView === 'departments' && <DepartmentsView />}
        {currentView === 'security' && <SecurityView />}
      </main>
    </div>
  )
}

export default App
