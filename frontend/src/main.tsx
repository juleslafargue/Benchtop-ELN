import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles.css'
import './themes.css'
import { applyPalette } from './theme'

applyPalette()  // this browser's last theme, before the account loads, so pages don't flash the default

// Chrome / Edge offer "Install Benchtop" (its own window and Dock icon) from the manifest alone; keep the offer so
// Account settings can show an Install button. No service worker: nothing is stored offline, so upgrades show at once.
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); (window as any).__benchtopInstall = e; window.dispatchEvent(new Event('benchtop:installable')) })
window.addEventListener('appinstalled', () => { (window as any).__benchtopInstall = null; window.dispatchEvent(new Event('benchtop:installable')) })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
