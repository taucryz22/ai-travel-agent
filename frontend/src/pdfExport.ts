import { jsPDF } from 'jspdf'
import type { DayPlan } from './types'

function toLines(city: string, days: DayPlan[]) {
  const lines: string[] = []

  lines.push(`Маршрут: ${city}`)
  lines.push('')

  for (const day of days) {
    lines.push(day.title)

    if (day.summary) {
      lines.push(`Фокус дня: ${day.summary.focus}`)
      lines.push(`Район дня: ${day.summary.area_label}`)
      lines.push(`Стиль дня: ${day.summary.style_label}`)
      lines.push(`Тема дня: ${day.summary.theme_label}`)
      lines.push(`В пути: ${day.summary.total_travel_min} мин`)
      lines.push(`Расстояние: ${day.summary.total_travel_km} км`)
      lines.push(`На посещение: ${day.summary.total_visit_min} мин`)
      lines.push(`Бюджет дня: ~ ${day.summary.estimated_day_budget_rub.toLocaleString('ru-RU')} ₽`)
      lines.push('')
    }

    for (const stop of day.stops) {
      lines.push(`${stop.start}–${stop.end} — ${stop.name}`)
      lines.push(`Адрес: ${stop.address}`)
      lines.push(`Категория: ${stop.category}`)
      lines.push(`На месте: ${stop.visit_duration_min} мин`)
      lines.push(`Цена: ~ ${stop.price_estimate_rub.toLocaleString('ru-RU')} ₽`)

      if (stop.travel_mode_label) {
        lines.push(
          `Переход: ${stop.travel_mode_label}, ${stop.travel_from_prev_min} мин, ${stop.travel_from_prev_km} км`
        )
      }

      if (stop.why_selected.length > 0) {
        lines.push('Почему выбрано:')
        for (const reason of stop.why_selected) {
          lines.push(`- ${reason}`)
        }
      }

      lines.push('')
    }

    lines.push('----------------------------------------')
    lines.push('')
  }

  return lines
}

export function exportPlanToPdf(city: string, days: DayPlan[]) {
  const doc = new jsPDF({
    orientation: 'p',
    unit: 'mm',
    format: 'a4',
  })

  const pageWidth = doc.internal.pageSize.getWidth()
  const pageHeight = doc.internal.pageSize.getHeight()
  const marginLeft = 12
  const marginTop = 14
  const maxTextWidth = pageWidth - marginLeft * 2
  const lineHeight = 6

  let y = marginTop

  doc.setFont('helvetica', 'normal')
  doc.setFontSize(12)

  const lines = toLines(city, days)

  for (const rawLine of lines) {
    const wrapped = doc.splitTextToSize(rawLine, maxTextWidth)

    for (const line of wrapped) {
      if (y > pageHeight - 14) {
        doc.addPage()
        y = marginTop
      }

      doc.text(String(line), marginLeft, y)
      y += lineHeight
    }
  }

  const safeCity = city
    .replace(/[^\p{L}\p{N}\-_ ]/gu, '')
    .trim()
    .replace(/\s+/g, '_') || 'travel-plan'

  doc.save(`${safeCity}_travel_plan.pdf`)
}
