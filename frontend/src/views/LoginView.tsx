import React, { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { User } from '../types'
import { Lock, User as UserIcon, Shield, CheckCircle2 } from 'lucide-react'

interface LoginViewProps {
  onSuccess: (user: User) => void
}

export const LoginView: React.FC<LoginViewProps> = ({ onSuccess }) => {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [step, setStep] = useState<'credentials' | '2fa'>('credentials')
  const [twoFactorCode, setTwoFactorCode] = useState('')
  const [maskedVkId, setMaskedVkId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [isTelegramWebApp, setIsTelegramWebApp] = useState(false)

  // Detect Telegram WebApp
  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp
    if (tg && tg.initData) {
      setIsTelegramWebApp(true)
      tg.ready()
      tg.expand()
      // Auto-authenticate via Telegram initData
      handleTelegramLogin(tg.initData)
    }
  }, [])

  const handleTelegramLogin = async (initData: string) => {
    setLoading(true)
    setError('')
    try {
      const res = await api.loginWithTelegram(initData)
      if (res.success && res.user) {
        onSuccess(res.user)
      }
    } catch (err: any) {
      console.warn('Авто-вход через Telegram не удался:', err.message)
      // Allow manual login if Telegram auth fails
    } finally {
      setLoading(false)
    }
  }

  const handleCredentialsSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const res = await api.login(username, password)
      if (res.needs_2fa) {
        setMaskedVkId(res.masked_vk_id || '***')
        setStep('2fa')
      } else if (res.user) {
        onSuccess(res.user)
      }
    } catch (err: any) {
      setError(err.message || 'Ошибка входа')
    } finally {
      setLoading(false)
    }
  }

  const handle2FASubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const res = await api.verify2FA(twoFactorCode)
      if (res.success && res.user) {
        onSuccess(res.user)
      }
    } catch (err: any) {
      setError(err.message || 'Неверный код 2FA')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
        background: 'radial-gradient(ellipse at 50% 20%, #1e293b, var(--bg-primary))',
      }}
    >
      <div className="glass-panel" style={{ width: '100%', maxWidth: 420, padding: 32 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div
            style={{
              width: 52,
              height: 52,
              borderRadius: 14,
              background: 'linear-gradient(135deg, #3b82f6, #6366f1)',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              marginBottom: 12,
              boxShadow: '0 8px 24px rgba(59, 130, 246, 0.4)',
            }}
          >
            <Shield size={28} color="#fff" />
          </div>
          <h2 style={{ fontSize: 22, fontWeight: 800, marginBottom: 6 }}>OSS Bot Web Panel</h2>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            {step === 'credentials' ? 'Вход в панель управления' : 'Подтверждение второго фактора'}
          </p>
        </div>

        {error && (
          <div
            style={{
              padding: '10px 14px',
              background: 'rgba(239, 68, 68, 0.15)',
              border: '1px solid var(--danger)',
              borderRadius: 8,
              color: 'var(--danger)',
              fontSize: 13,
              marginBottom: 18,
            }}
          >
            {error}
          </div>
        )}

        {isTelegramWebApp && (
          <div
            style={{
              marginBottom: 18,
              padding: '8px 12px',
              background: 'rgba(59, 130, 246, 0.15)',
              borderRadius: 8,
              fontSize: 12,
              color: 'var(--accent)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <CheckCircle2 size={16} />
            Запущено внутри Telegram Mini App
          </div>
        )}

        {step === 'credentials' ? (
          <form onSubmit={handleCredentialsSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Имя пользователя
              </label>
              <div style={{ position: 'relative' }}>
                <UserIcon size={16} style={{ position: 'absolute', left: 12, top: 13, color: 'var(--text-secondary)' }} />
                <input
                  type="text"
                  className="input"
                  placeholder="admin"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  style={{ paddingLeft: 38 }}
                  required
                />
              </div>
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Пароль
              </label>
              <div style={{ position: 'relative' }}>
                <Lock size={16} style={{ position: 'absolute', left: 12, top: 13, color: 'var(--text-secondary)' }} />
                <input
                  type="password"
                  className="input"
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  style={{ paddingLeft: 38 }}
                  required
                />
              </div>
            </div>

            <button type="submit" disabled={loading} className="btn btn-primary" style={{ marginTop: 8 }}>
              {loading ? 'Проверка...' : 'Войти'}
            </button>
          </form>
        ) : (
          <form onSubmit={handle2FASubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ textAlign: 'center', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
              Код подтверждения отправлен в VK на аккаунт <strong>{maskedVkId}</strong>.
            </div>

            <div>
              <input
                type="text"
                className="input"
                placeholder="6-значный код"
                value={twoFactorCode}
                onChange={(e) => setTwoFactorCode(e.target.value)}
                maxLength={6}
                style={{ textAlign: 'center', fontSize: 20, letterSpacing: 6, fontWeight: 700 }}
                autoFocus
                required
              />
            </div>

            <button type="submit" disabled={loading} className="btn btn-primary">
              {loading ? 'Проверка...' : 'Подтвердить вход'}
            </button>

            <button
              type="button"
              onClick={() => {
                setStep('credentials')
                setTwoFactorCode('')
              }}
              className="btn btn-secondary"
            >
              Вернуться назад
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
