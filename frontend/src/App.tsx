import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { api, getToken, setToken, setUnauthorizedHandler, type Notebook, type User } from './api'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import NotebookPage from './pages/NotebookPage'
import ExperimentPage from './pages/ExperimentPage'
import Materials from './pages/Materials'
import MaterialDetail from './pages/MaterialDetail'
import Inventory from './pages/Inventory'
import Search from './pages/Search'
import Reactions from './pages/Reactions'
import Templates from './pages/Templates'
import Admin from './pages/Admin'
import Account from './pages/Account'

interface Ctx {
  user: User
  notebooks: Notebook[]
  refreshNotebooks: () => Promise<void>
  toast: (msg: string) => void
  logout: () => void
  canWrite: boolean
  setUser: (u: User) => void
}
const AppCtx = createContext<Ctx>(null as unknown as Ctx)
export const useApp = () => useContext(AppCtx)

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(!!getToken())
  const [notebooks, setNotebooks] = useState<Notebook[]>([])
  const [toastMsg, setToastMsg] = useState<string | null>(null)

  const logout = useCallback(() => { setToken(null); setUser(null) }, [])
  useEffect(() => setUnauthorizedHandler(logout), [logout])

  useEffect(() => {
    if (!getToken()) return
    api.get<User>('/api/auth/me').then(setUser).catch(() => setToken(null)).finally(() => setBooting(false))
  }, [])

  const refreshNotebooks = useCallback(async () => {
    setNotebooks(await api.get<Notebook[]>('/api/notebooks'))
  }, [])
  useEffect(() => { if (user) refreshNotebooks() }, [user, refreshNotebooks])

  const toast = useCallback((msg: string) => {
    setToastMsg(msg)
    window.setTimeout(() => setToastMsg(m => (m === msg ? null : m)), 2800)
  }, [])

  useEffect(() => {
    const t = user?.preferences?.theme
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t
    else delete document.documentElement.dataset.theme
  }, [user?.preferences?.theme])

  if (booting) return null
  if (!user) return <Login onLogin={(t, u) => { setToken(t); setUser(u) }} />

  return (
    <AppCtx.Provider value={{ user, notebooks, refreshNotebooks, toast, logout, canWrite: user.role !== 'viewer', setUser }}>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/notebooks/:id" element={<NotebookPage />} />
          <Route path="/experiments/:id" element={<ExperimentPage />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/materials" element={<Materials />} />
          <Route path="/materials/:id" element={<MaterialDetail />} />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/search" element={<Search />} />
          <Route path="/reactions" element={<Reactions />} />
          <Route path="/account" element={<Account />} />
          <Route path="/admin" element={user.role === 'admin' ? <Admin /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
      {toastMsg && <div className="toast" role="status">{toastMsg}</div>}
    </AppCtx.Provider>
  )
}
