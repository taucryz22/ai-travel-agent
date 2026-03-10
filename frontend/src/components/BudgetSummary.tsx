import type { BudgetSummary as BudgetSummaryType } from '../types'

function statusLabel(status: BudgetSummaryType['status']) {
  if (status === 'ok') return 'В бюджете'
  if (status === 'near_limit') return 'Почти на лимите'
  return 'Выше бюджета'
}

export default function BudgetSummary({ summary }: { summary: BudgetSummaryType }) {
  const deltaAbs = Math.abs(summary.delta_rub)

  return (
    <div className={`card budget-box budget-${summary.status}`}>
      <div className="budget-topline">
        <strong>Сводка по бюджету</strong>
        <span className={`badge budget-badge ${summary.status}`}>{statusLabel(summary.status)}</span>
      </div>

      <div className="budget-grid">
        <div>
          <div className="muted">Бюджет</div>
          <div className="budget-value">{summary.budget_total.toLocaleString('ru-RU')} {summary.currency}</div>
        </div>
        <div>
          <div className="muted">Оценка</div>
          <div className="budget-value">{summary.estimated_total.toLocaleString('ru-RU')} {summary.currency}</div>
        </div>
      </div>

      <div className="muted">
        {summary.delta_rub >= 0
          ? `Запас: ${deltaAbs.toLocaleString('ru-RU')} ${summary.currency}`
          : `Превышение: ${deltaAbs.toLocaleString('ru-RU')} ${summary.currency}`}
      </div>

      <div className="muted">{summary.notes}</div>
    </div>
  )
}