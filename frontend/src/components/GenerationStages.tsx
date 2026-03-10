const STAGES = ['Интент', 'RAG', 'Поиск мест', 'Построение маршрута']

export default function GenerationStages({ activeStage }: { activeStage: number }) {
  return (
    <div className="stages-box">
      {STAGES.map((stage, index) => {
        const state = index < activeStage ? 'done' : index === activeStage ? 'active' : 'idle'
        return (
          <div key={stage} className={`stage-pill ${state}`}>
            <span className="stage-index">{index + 1}</span>
            <span>{stage}</span>
          </div>
        )
      })}
    </div>
  )
}
