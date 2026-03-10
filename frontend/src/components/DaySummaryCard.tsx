import type { DaySummary } from '../types'

function kmLabel(km: number) {
  if (!km || km <= 0) return '0 км'
  return `${km.toFixed(km < 10 ? 1 : 0)} км`
}

export default function DaySummaryCard({ summary }: { summary: DaySummary }) {
  return (
    <div className="card day-summary-card">
      <div className="day-summary-grid day-summary-grid-top">
        <div>
          <div className="muted">Фокус дня</div>
          <div className="day-summary-value">{summary.focus}</div>
        </div>
        <div>
          <div className="muted">Район дня</div>
          <div className="day-summary-value">{summary.area_label}</div>
        </div>
        <div>
          <div className="muted">Стиль дня</div>
          <div className="day-summary-value">{summary.style_label}</div>
        </div>
        <div>
          <div className="muted">Ключевая тема</div>
          <div className="day-summary-value">{summary.theme_label}</div>
        </div>
      </div>

      <div className="day-summary-grid">
        <div>
          <div className="muted">Точек</div>
          <div className="day-summary-value">{summary.stops_count}</div>
        </div>
        <div>
          <div className="muted">В пути</div>
          <div className="day-summary-value">{summary.total_travel_min} мин</div>
        </div>
        <div>
          <div className="muted">Расстояние</div>
          <div className="day-summary-value">{kmLabel(summary.total_travel_km)}</div>
        </div>
        <div>
          <div className="muted">На посещение</div>
          <div className="day-summary-value">{summary.total_visit_min} мин</div>
        </div>
        <div>
          <div className="muted">Оценка дня</div>
          <div className="day-summary-value">≈ {summary.estimated_day_budget_rub.toLocaleString('ru-RU')} ₽</div>
        </div>
      </div>
    </div>
  )
}
