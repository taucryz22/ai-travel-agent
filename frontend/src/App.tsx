import { useEffect, useMemo, useState } from 'react'
import { generatePlan } from './api'
import type { ItineraryResponse, PlanRequest, Stop } from './types'
import { itineraryToText } from './yandexLinks'
import { exportPlanToPdf } from './pdfExport'
import TripForm from './components/TripForm'
import DayTabs from './components/DayTabs'
import Timeline from './components/Timeline'
import BudgetSummary from './components/BudgetSummary'
import GenerationStages from './components/GenerationStages'

const STAGE_COUNT = 4

export default function App() {
  const [data, setData] = useState<ItineraryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeDay, setActiveDay] = useState(0)
  const [lastRequest, setLastRequest] = useState<PlanRequest | null>(null)
  const [loadingStage, setLoadingStage] = useState(0)
  const [replacingStopName, setReplacingStopName] = useState<string | null>(null)

  const activeDayData = data?.days[activeDay]
  const copyText = useMemo(() => (data ? itineraryToText(data.city, data.days) : ''), [data])

  useEffect(() => {
    if (!loading) {
      setLoadingStage(0)
      return
    }

    setLoadingStage(0)
    const timer = window.setInterval(() => {
      setLoadingStage((prev) => Math.min(prev + 1, STAGE_COUNT - 1))
    }, 900)

    return () => window.clearInterval(timer)
  }, [loading])

  async function runPlan(payload: PlanRequest) {
    setLoading(true)
    setError(null)
    try {
      const result = await generatePlan(payload)
      setData(result)
      setLastRequest(payload)
      setActiveDay(0)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка')
    } finally {
      setLoading(false)
      setReplacingStopName(null)
    }
  }

  async function handleSubmit(payload: PlanRequest) {
    await runPlan(payload)
  }

  async function copyPlan() {
    if (!copyText) return
    await navigator.clipboard.writeText(copyText)
  }

  function downloadTxt() {
    if (!copyText) return
    const blob = new Blob([copyText], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'travel-plan.txt'
    a.click()
    URL.revokeObjectURL(url)
  }

  function downloadPdf() {
    if (!data) return
    exportPlanToPdf(data.city, data.days)
  }

  async function regenerateDay() {
    if (!lastRequest || !activeDayData) return
    const avoidPlaceNames = activeDayData.stops.map((stop) => stop.name)
    await runPlan({ ...lastRequest, avoid_place_names: [...(lastRequest.avoid_place_names || []), ...avoidPlaceNames] })
  }

  async function replaceStop(stop: Stop) {
    if (!lastRequest) return
    setReplacingStopName(stop.name)
    await runPlan({ ...lastRequest, avoid_place_names: [...(lastRequest.avoid_place_names || []), stop.name] })
  }

  return (
    <div className="page">
      <header className="hero">
        <div>
          <h1>AI-Travel Agent</h1>
          <p>Больше впечатлений, меньше вкладок в браузере</p>
        </div>
      </header>

      <TripForm onSubmit={handleSubmit} loading={loading} />

      {error && <div className="card error">{error}</div>}

      {loading && (
        <div className="card loading-box">
          <strong>Генерируем маршрут…</strong>
          <div className="muted">Маршрут собирается по этапам, чтобы итоговый план был логичным и выполнимым.</div>
          <GenerationStages activeStage={loadingStage} />
        </div>
      )}

      {data && (
        <section className="results">
          <div className="results-header">
            <div>
              <h2>{data.city}</h2>
              <div className="muted">Общее время в пути: {data.metrics.total_travel_min} мин</div>
              <div className="muted">Дней в плане: {data.days.length}</div>
              <div className="muted">
                Режим: {
                  data.request.mode === 'smart'
                    ? 'Умный режим'
                    : data.request.mode === 'walking'
                      ? 'Пешком'
                      : data.request.mode === 'transit'
                        ? 'Общественный транспорт'
                        : 'На машине'
                }
              </div>
            </div>
            <BudgetSummary summary={data.budget_summary} />
          </div>

          <div className="toolbar">
            <button onClick={copyPlan}>Скопировать план</button>
            <button onClick={downloadTxt}>Скачать .txt</button>
            <button onClick={downloadPdf}>Скачать PDF</button>
            {data.sources.wikivoyage_page && (
              <a href={data.sources.wikivoyage_page} target="_blank" rel="noreferrer">Открыть Wikivoyage</a>
            )}
          </div>

          <div className="card sources-box">
            <h3>Как сервис собрал маршрут</h3>
            <div className="sources-grid">
              <div>
                <strong>Подсказки из путеводителя</strong>
                <ul>
                  {data.sources.rag_snippets.map((snippet, idx) => <li key={idx}>{snippet}</li>)}
                </ul>
              </div>
              <div>
                <strong>Поисковые фразы для Yandex Places</strong>
                <ul>
                  {data.sources.generated_search_phrases.map((phrase, idx) => <li key={idx}>{phrase}</li>)}
                </ul>
              </div>
            </div>
          </div>

          {data.metrics.violations.length > 0 && (
            <div className="card violations">
              <h3>Проверки маршрута</h3>
              <ul>
                {data.metrics.violations.map((v, idx) => (
                  <li key={idx}>
                    <strong>{v.type}</strong>: {v.value_min} мин — {v.note}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <DayTabs days={data.days} activeIndex={activeDay} onChange={setActiveDay} />
          {activeDayData && (
            <Timeline
              day={activeDayData}
              onRegenerateDay={regenerateDay}
              regenerating={loading}
              onReplaceStop={replaceStop}
              replacingStopName={replacingStopName}
            />
          )}
        </section>
      )}
    </div>
  )
}
