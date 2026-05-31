import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import {
  artifactDownloadUrl,
  createJob,
  fetchArtifacts,
  fetchJob,
  fetchPeople,
  fetchReport,
  requestExportBundle
} from './api'
import './App.css'
import type { Artifact, JobView, PersonProfile, VideoReport } from './types'

type TabId = 'subtitles' | 'video' | 'people' | 'summary'

const PIPELINE_STEPS = [
  { key: 'ingest', label: 'Ingest' },
  { key: 'audio_extract', label: 'Audio Extract' },
  { key: 'asr', label: 'Whisper ASR' },
  { key: 'subtitle_postprocess', label: 'Subtitles' },
  { key: 'vision', label: 'OpenVINO Vision' },
  { key: 'speaker_attribution', label: 'Speaker Attribution' },
  { key: 'report', label: 'Ollama Summary' },
  { key: 'burned_video', label: 'Burned MP4' },
  { key: 'done', label: 'Done' }
] as const

function progressLabel(job: JobView | null): string {
  if (!job) return 'Waiting for first upload'
  if (job.status === 'failed') return `Failed: ${job.error_message ?? 'Unknown error'}`
  if (job.status === 'completed') return 'Completed'
  return `${job.current_step ?? 'processing'}`
}

function formatSeconds(value: number): string {
  return `${value.toFixed(1)}s`
}

function App() {
  const [file, setFile] = useState<File | null>(null)
  const [language, setLanguage] = useState('')
  const [qualityPreset, setQualityPreset] = useState<'max_quality' | 'balanced' | 'max_speed'>(
    'max_quality'
  )
  const [job, setJob] = useState<JobView | null>(null)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [people, setPeople] = useState<PersonProfile[]>([])
  const [report, setReport] = useState<VideoReport | null>(null)
  const [activeTab, setActiveTab] = useState<TabId>('subtitles')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedExportFormats, setSelectedExportFormats] = useState<string[]>([
    'srt',
    'vtt',
    'ass',
    'mp4_burned'
  ])

  const completed = job?.status === 'completed'
  const isRunning = job?.status === 'queued' || job?.status === 'running'

  const subtitleArtifacts = useMemo(
    () => artifacts.filter((item) => ['subtitle_srt', 'subtitle_vtt', 'subtitle_ass'].includes(item.kind)),
    [artifacts]
  )
  const burnedArtifact = useMemo(
    () => artifacts.find((item) => item.kind === 'video_burned'),
    [artifacts]
  )
  const zipArtifact = useMemo(() => artifacts.find((item) => item.kind === 'export_zip'), [artifacts])

  const hydrateResults = async (jobId: string) => {
    const [nextArtifacts, nextPeople, nextReport] = await Promise.all([
      fetchArtifacts(jobId),
      fetchPeople(jobId),
      fetchReport(jobId)
    ])
    setArtifacts(nextArtifacts)
    setPeople(nextPeople)
    setReport(nextReport)
  }

  useEffect(() => {
    if (!job || !isRunning) return
    const timer = window.setInterval(async () => {
      try {
        const updated = await fetchJob(job.id)
        setJob(updated)
        if (updated.status === 'completed') {
          await hydrateResults(updated.id)
        }
      } catch (pollError) {
        setError((pollError as Error).message)
      }
    }, 1800)
    return () => window.clearInterval(timer)
  }, [job, isRunning])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!file) {
      setError('Pick a video file first.')
      return
    }
    setError(null)
    setIsSubmitting(true)
    setArtifacts([])
    setPeople([])
    setReport(null)
    try {
      const created = await createJob({
        file,
        language: language.trim() || undefined,
        autoDetectLanguage: language.trim().length === 0,
        qualityPreset,
        detectPeople: true,
        generateSummary: true,
        enableActiveSpeakerModel: true,
        exportFormats: selectedExportFormats
      })
      setJob(created)
      setActiveTab('subtitles')
    } catch (createError) {
      setError((createError as Error).message)
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleBundleExport() {
    if (!job) return
    setError(null)
    try {
      const nextArtifacts = await requestExportBundle(job.id, selectedExportFormats)
      setArtifacts(nextArtifacts)
    } catch (bundleError) {
      setError((bundleError as Error).message)
    }
  }

  function toggleFormat(format: string) {
    setSelectedExportFormats((current) =>
      current.includes(format) ? current.filter((item) => item !== format) : [...current, format]
    )
  }

  function portraitUrl(person: PersonProfile): string | null {
    if (!job || !person.portrait_path) return null
    const filename = person.portrait_path.split(/[\\/]/).pop()
    if (!filename) return null
    return artifactDownloadUrl(job.id, filename)
  }

  return (
    <main className="page">
      <header className="hero">
        <div className="hero-content">
          <p className="eyebrow">NewtonSpect V1</p>
          <h1>Automatic Subtitles + Video Intelligence</h1>
          <p className="subtitle">
            Upload video, track processing live, download SRT/VTT/ASS, burned MP4, and AI summary with people insights.
          </p>
        </div>
        <div className="orb-grid" aria-hidden>
          <span />
          <span />
          <span />
        </div>
      </header>

      <section className="workspace">
        <article className="panel upload-panel">
          <h2>Upload</h2>
          <form onSubmit={onSubmit} className="upload-form">
            <label className="field">
              <span>Video file</span>
              <input
                type="file"
                accept="video/*"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>

            <label className="field">
              <span>Language (optional)</span>
              <input
                type="text"
                placeholder="ru, en, de ..."
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              />
            </label>

            <label className="field">
              <span>Quality profile</span>
              <select
                value={qualityPreset}
                onChange={(event) =>
                  setQualityPreset(event.target.value as 'max_quality' | 'balanced' | 'max_speed')
                }
              >
                <option value="max_quality">Max quality (large-v3)</option>
                <option value="balanced">Balanced</option>
                <option value="max_speed">Max speed</option>
              </select>
            </label>

            <div className="chips">
              {['srt', 'vtt', 'ass', 'mp4_burned'].map((format) => (
                <button
                  key={format}
                  type="button"
                  className={selectedExportFormats.includes(format) ? 'chip active' : 'chip'}
                  onClick={() => toggleFormat(format)}
                >
                  {format}
                </button>
              ))}
            </div>

            <button className="primary" type="submit" disabled={isSubmitting}>
              {isSubmitting ? 'Creating job...' : 'Start Processing'}
            </button>
          </form>
        </article>

        <article className="panel progress-panel">
          <h2>Pipeline</h2>
          <p className="status">{progressLabel(job)}</p>
          <div className="progress-shell">
            <div
              className={job?.status === 'failed' ? 'progress-value failed' : 'progress-value'}
              style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }}
            />
          </div>
          <div className="step-list">
            {PIPELINE_STEPS.map((step, index) => {
              const currentIndex = PIPELINE_STEPS.findIndex((x) => x.key === (job?.current_step ?? 'ingest'))
              const done = job?.status === 'completed' || index < currentIndex
              const active = job?.status === 'running' && index === currentIndex
              return (
                <div
                  key={step.key}
                  className={done ? 'step done' : active ? 'step active' : 'step'}
                >
                  <span>{step.label}</span>
                </div>
              )
            })}
          </div>
        </article>
      </section>

      {error && <p className="error-banner">{error}</p>}

      {job && (
        <section className="panel result-panel">
          <header className="result-header">
            <div>
              <h2>Job {job.id.slice(0, 8)}</h2>
              <p className="job-meta">
                {job.original_filename} · {Math.round(job.progress * 100)}% · {job.status}
              </p>
            </div>
            {completed && (
              <button className="secondary" type="button" onClick={handleBundleExport}>
                Build ZIP Bundle
              </button>
            )}
          </header>

          <div className="tab-row">
            {[
              ['subtitles', 'Subtitles'],
              ['video', 'Video Export'],
              ['people', 'People'],
              ['summary', 'AI Summary']
            ].map(([id, label]) => (
              <button
                key={id}
                className={activeTab === id ? 'tab active' : 'tab'}
                type="button"
                onClick={() => setActiveTab(id as TabId)}
              >
                {label}
              </button>
            ))}
          </div>

          {activeTab === 'subtitles' && (
            <div className="tab-content grid-list">
              {subtitleArtifacts.map((artifact) => (
                <a
                  className="artifact-link"
                  key={artifact.name}
                  href={artifactDownloadUrl(job.id, artifact.name)}
                >
                  <strong>{artifact.name}</strong>
                  <span>{artifact.kind}</span>
                </a>
              ))}
              {subtitleArtifacts.length === 0 && <p>No subtitle files yet.</p>}
            </div>
          )}

          {activeTab === 'video' && (
            <div className="tab-content">
              {burnedArtifact ? (
                <a className="primary ghost" href={artifactDownloadUrl(job.id, burnedArtifact.name)}>
                  Download burned MP4
                </a>
              ) : (
                <p>Burned MP4 not available yet.</p>
              )}
              {zipArtifact && (
                <a className="primary ghost" href={artifactDownloadUrl(job.id, zipArtifact.name)}>
                  Download ZIP bundle
                </a>
              )}
            </div>
          )}

          {activeTab === 'people' && (
            <div className="tab-content people-grid">
              {people.map((person) => (
                <article className="person-card" key={person.person_id}>
                  {portraitUrl(person) ? (
                    <img src={portraitUrl(person) ?? undefined} alt={person.person_id} />
                  ) : (
                    <div className="portrait-fallback">{person.person_id}</div>
                  )}
                  <h3>{person.person_id}</h3>
                  <p>Screen: {formatSeconds(person.track_stats.screen_time_seconds)}</p>
                  <p>Speaking: {formatSeconds(person.track_stats.speaking_seconds)}</p>
                  <p>Confidence: {person.track_stats.avg_confidence.toFixed(3)}</p>
                  <ul>
                    {(person.key_comments.length ? person.key_comments : ['No extracted comments yet.']).map(
                      (comment, idx) => (
                        <li key={idx}>{comment}</li>
                      )
                    )}
                  </ul>
                </article>
              ))}
              {people.length === 0 && <p>People analysis is not ready yet.</p>}
            </div>
          )}

          {activeTab === 'summary' && (
            <div className="tab-content summary">
              {report ? (
                <>
                  <section>
                    <h3>Summary</h3>
                    <pre>{report.summary_md || 'Summary is empty.'}</pre>
                  </section>
                  <section>
                    <h3>Topics</h3>
                    <ul>
                      {report.key_topics.map((topic) => (
                        <li key={topic}>{topic}</li>
                      ))}
                      {report.key_topics.length === 0 && <li>No topics extracted.</li>}
                    </ul>
                  </section>
                  <section>
                    <h3>LaTeX</h3>
                    <ul>
                      {report.latex_blocks.map((formula) => (
                        <li key={formula}>
                          <code>{formula}</code>
                        </li>
                      ))}
                      {report.latex_blocks.length === 0 && <li>No formulas detected.</li>}
                    </ul>
                  </section>
                </>
              ) : (
                <p>AI summary is not ready yet.</p>
              )}
            </div>
          )}
        </section>
      )}
    </main>
  )
}

export default App
