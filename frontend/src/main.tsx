import { createRoot } from 'react-dom/client'
import App from './App'
import { applyAppearance, readAppearance } from './appearance'
import './styles.css'
import './workbench-theme.css'
import './mode-menu.css'

applyAppearance(readAppearance())
createRoot(document.getElementById('root')!).render(<App />)
