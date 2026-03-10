import type { DayPlan } from './types'

export function itineraryToText(city: string, days: DayPlan[]): string {
  const lines: string[] = []
  lines.push(`План поездки: ${city}`)
  lines.push('')

  for (const day of days) {
    lines.push(day.title)
    if (day.summary) {
      lines.push(
        `Фокус: ${day.summary.focus} | Точек: ${day.summary.stops_count} | В пути: ${day.summary.total_travel_min} мин | Бюджет дня: ~${day.summary.estimated_day_budget_rub} ₽`
      )
    }

    for (const stop of day.stops) {
      const travelInfo = stop.travel_from_prev_min > 0
        ? ` | Переезд: ${stop.travel_from_prev_min} мин${stop.travel_mode_label ? ` (${stop.travel_mode_label})` : ''}`
        : ''

      lines.push(
        `${stop.start}–${stop.end} — ${stop.name}, ${stop.address}${travelInfo}, ~${stop.price_estimate_rub} ₽`
      )
    }

    lines.push('')
  }

  return lines.join('\n')
}