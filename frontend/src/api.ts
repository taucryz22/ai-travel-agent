import type { ItineraryResponse, PlanRequest } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

export async function generatePlan(payload: PlanRequest): Promise<ItineraryResponse> {
  const resp = await fetch(`${API_BASE}/api/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(text || 'Failed to generate plan')
  }

  return resp.json()
}
