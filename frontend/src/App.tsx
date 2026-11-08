import { BrowserRouter } from 'react-router-dom'
import { AppRoutes } from './AppRoutes'
import { AuthProvider } from './features/auth/AuthProvider'
import { NotificationsProvider } from './features/notifications/NotificationsProvider'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <NotificationsProvider>
          <AppRoutes />
        </NotificationsProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
