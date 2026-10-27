import { useEffect, useState } from 'react'
import LoginPage from './components/LoginPage'
import AppShell from './components/AppShell'
import { getCurrentUser } from './lib/api'

function App() {
  const [isDark, setIsDark] = useState(false)
  const [session, setSession] = useState(null)
  const [isRestoring, setIsRestoring] = useState(true)

  useEffect(() => {
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light'
  }, [isDark])

  useEffect(() => {
    const token = localStorage.getItem('umms_token')
    if (!token) {
      setIsRestoring(false)
      return
    }

    getCurrentUser(token)
      .then((user) => setSession({ access_token: token, user }))
      .catch(() => localStorage.removeItem('umms_token'))
      .finally(() => setIsRestoring(false))
  }, [])

  function handleLoginSuccess(data) {
    localStorage.setItem('umms_token', data.access_token)
    setSession(data)
  }

  function handleLogout() {
    localStorage.removeItem('umms_token')
    setSession(null)
  }

  if (isRestoring) {
    return null
  }

  if (session) {
    return (
      <AppShell
        session={session}
        isDark={isDark}
        onToggleTheme={() => setIsDark((v) => !v)}
        onLogout={handleLogout}
      />
    )
  }

  return (
    <LoginPage
      isDark={isDark}
      onToggleTheme={() => setIsDark((v) => !v)}
      onLoginSuccess={handleLoginSuccess}
    />
  )
}

export default App
