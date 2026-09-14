import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import 'maplibre-gl/dist/maplibre-gl.css'
import './styles.css'
import { App } from './app'

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 8_000, refetchOnWindowFocus: false } } })
createRoot(document.getElementById('root')!).render(<StrictMode><QueryClientProvider client={client}><App /></QueryClientProvider></StrictMode>)
