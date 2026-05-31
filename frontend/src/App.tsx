import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import type { Artifact, JobView, PersonProfile, VideoReport } from './types'
import {
  artifactDownloadUrl,
  createJob,
  createJobChunked,
  fetchArtifacts,
  fetchJob,
  fetchJobLibrary,
  fetchPeople,
  fetchReport,
  type UploadProgress,
  requestExportBundle
} from './api'
import './App.css'

type TabId = 'subtitles' | 'video' | 'people' | 'summary'
type Locale = 'ru' | 'en'

const I18N: Record<
  Locale,
  Record<
    | 'title'
    | 'subtitle'
    | 'upload'
    | 'pipeline'
    | 'storage'
    | 'file'
    | 'language'
    | 'quality'
    | 'features'
    | 'mask'
    | 'subtitles'
    | 'burned'
    | 'vision'
    | 'summary'
    | 'speaker'
    | 'formats'
    | 'start'
    | 'creating'
    | 'downloadBundle'
    | 'job'
    | 'speed'
    | 'eta'
    | 'deviceAuthor'
    | 'lastResults'
    | 'open'
    | 'status'
    | 'runtime'
    | 'done'
    | 'waiting'
    | 'noLibrary'
    | 'mobileHint',
    string
  >
> = {
  en: {
    title: 'Automatic Subtitles + Video Intelligence',
    subtitle:
      'Real progress, ETA and speed for every stage. Mobile-ready upload/download on iPhone and Android.',
    upload: 'Upload',
    pipeline: 'Pipeline Runtime',
    storage: 'Storage',
    file: 'Video file',
    language: 'Language (optional)',
    quality: 'Quality profile',
    features: 'Optional features',
    mask: 'Mask overlay (OpenVINO data)',
    subtitles: 'Generate subtitles',
    burned: 'Generate burned MP4',
    vision: 'Run people detection',
    summary: 'Generate AI summary',
    speaker: 'Speaker attribution',
    formats: 'Export formats',
    start: 'Start Processing',
    creating: 'Creating job...',
    downloadBundle: 'Build ZIP Bundle',
    job: 'Job',
    speed: 'Speed',
    eta: 'ETA',
    deviceAuthor: 'Device author',
    lastResults: 'Stored results',
    open: 'Open',
    status: 'Status',
    runtime: 'Runtime',
    done: 'Completed',
    waiting: 'Waiting for first upload',
    noLibrary: 'No completed or queued jobs yet.',
    mobileHint: 'Tip: on mobile you can pick from gallery or record directly.'
  },
  ru: {
    title: 'Автосубтитры и видео-аналитика',
    subtitle:
      'Реальный прогресс, ETA и скорость на каждом этапе. Мобильная загрузка/скачивание на iPhone и Android.',
    upload: 'Загрузка',
    pipeline: 'Прогресс пайплайна',
    storage: 'Хранилище',
    file: 'Видео файл',
    language: 'Язык (опционально)',
    quality: 'Профиль качества',
    features: 'Опциональные функции',
    mask: 'Маска (данные OpenVINO)',
    subtitles: 'Генерировать субтитры',
    burned: 'Генерировать MP4 с вшитыми субтитрами',
    vision: 'Детекция людей',
    summary: 'Генерировать AI-конспект',
    speaker: 'Атрибуция говорящих',
    formats: 'Форматы экспорта',
    start: 'Запустить обработку',
    creating: 'Создание задачи...',
    downloadBundle: 'Собрать ZIP пакет',
    job: 'Задача',
    speed: 'Скорость',
    eta: 'Осталось',
    deviceAuthor: 'Автор (устройство)',
    lastResults: 'Сохраненные результаты',
    open: 'Открыть',
    status: 'Статус',
    runtime: 'Ход выполнения',
    done: 'Готово',
    waiting: 'Ожидание первой загрузки',
    noLibrary: 'Пока нет сохраненных задач.',
    mobileHint: 'Подсказка: на телефоне можно выбрать видео из галереи или записать сразу.'
  }
}

const STEP_LABELS: Record<string, { en: string; ru: string }> = {
  ingest: { en: 'Ingest', ru: 'Инициализация' },
  audio_extract: { en: 'Audio extract', ru: 'Извлечение аудио' },
  asr: { en: 'Whisper ASR', ru: 'Распознавание речи' },
  subtitle_postprocess: { en: 'Subtitles', ru: 'Субтитры' },
  vision: { en: 'OpenVINO vision', ru: 'OpenVINO анализ' },
  speaker_attribution: { en: 'Speaker attribution', ru: 'Кто говорит' },
  report: { en: 'AI summary', ru: 'AI-конспект' },
  burned_video: { en: 'Burned MP4', ru: 'Вшивание субтитров' },
  mask_overlay: { en: 'Mask overlay', ru: 'Видео с масками' },
  done: { en: 'Done', ru: 'Готово' }
}

const STAGE_ORDER = [
  'ingest',
  'audio_extract',
  'asr',
  'subtitle_postprocess',
  'vision',
  'speaker_attribution',
  'report',
  'burned_video',
  'mask_overlay',
  'done'
]

function formatSeconds(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return '-'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const sec = Math.round(seconds % 60)
  return `${mins}m ${sec}s`
}

function formatSpeed(speed: number | null | undefined, unit: string | null | undefined): string {
  if (speed == null || Number.isNaN(speed) || !unit) return '-'
  return `${speed.toFixed(2)} ${unit}`
}

function formatBytesPerSecond(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value) || value <= 0) return '-'
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s']
  let speed = value
  let unitIndex = 0
  while (speed >= 1024 && unitIndex < units.length - 1) {
    speed /= 1024
    unitIndex += 1
  }
  return `${speed.toFixed(2)} ${units[unitIndex]}`
}

function currentStageLabel(job: JobView | null, locale: Locale, t: Record<string, string>): string {
  if (!job) return t.waiting
  if (job.status === 'failed') return job.error_message || 'Failed'
  if (job.status === 'completed') return t.done
  const key = job.current_step || 'ingest'
  return STEP_LABELS[key]?.[locale] ?? key
}

function App() {
  const [locale, setLocale] = useState<Locale>('ru')
  const t = I18N[locale]

  const [file, setFile] = useState<File | null>(null)
  const [language, setLanguage] = useState('')
  const [qualityPreset, setQualityPreset] = useState<'max_quality' | 'balanced' | 'max_speed'>(
    'max_quality'
  )
  const [selectedExportFormats, setSelectedExportFormats] = useState<string[]>([
    'srt',
    'vtt',
    'ass',
    'mp4_burned'
  ])
  const [options, setOptions] = useState({
    detectPeople: true,
    generateSummary: true,
    enableActiveSpeakerModel: true,
    enableSubtitles: true,
    enableBurnedVideo: true,
    enableMaskOverlay: false
  })

  const [job, setJob] = useState<JobView | null>(null)
  const [jobLibrary, setJobLibrary] = useState<JobView[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [people, setPeople] = useState<PersonProfile[]>([])
  const [report, setReport] = useState<VideoReport | null>(null)
  const [activeTab, setActiveTab] = useState<TabId>('subtitles')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)

  const completed = job?.status === 'completed'
  const isRunning = job?.status === 'queued' || job?.status === 'running'

  const subtitleArtifacts = useMemo(
    () =>
      artifacts.filter((item) =>
        ['subtitle_srt', 'subtitle_vtt', 'subtitle_ass', 'transcript_json'].includes(item.kind)
      ),
    [artifacts]
  )
  const burnedArtifact = useMemo(
    () =>
      artifacts.find((item) => item.kind === 'video_burned') ||
      artifacts.find((item) => item.kind === 'video_masked'),
    [artifacts]
  )
  const zipArtifact = useMemo(() => artifacts.find((item) => item.kind === 'export_zip'), [artifacts])
  const videoArtifact = useMemo(
    () =>
      artifacts.find((item) => item.kind === 'video_masked') ||
      artifacts.find((item) => item.kind === 'video_burned'),
    [artifacts]
  )
  const visibleStages = useMemo(() => {
    if (!job) return []
    const currentIndex = STAGE_ORDER.indexOf(job.current_step ?? 'ingest')
    return STAGE_ORDER.filter((step, index) => {
      const hasRuntime = Boolean(job.runtime.stages?.[step])
      return hasRuntime || index <= Math.max(currentIndex, 0)
    })
  }, [job])
  const inputPreviewUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file])
  const videoPlaybackUrl =
    job && videoArtifact ? artifactDownloadUrl(job.id, videoArtifact.name) : inputPreviewUrl

  const hydrateResults = async (jobId: string) => {
    const [nextArtifacts, nextPeople, nextReport] = await Promise.allSettled([
      fetchArtifacts(jobId),
      fetchPeople(jobId),
      fetchReport(jobId)
    ])
    if (nextArtifacts.status === 'fulfilled') {
      setArtifacts(nextArtifacts.value)
    }
    if (nextPeople.status === 'fulfilled') {
      setPeople(nextPeople.value)
    }
    if (nextReport.status === 'fulfilled') {
      setReport(nextReport.value)
    }
  }

  const refreshLibrary = async () => {
    try {
      const items = await fetchJobLibrary(100)
      setJobLibrary(items)
    } catch (libraryError) {
      setError((libraryError as Error).message)
    }
  }

  useEffect(() => {
    let cancelled = false
    fetchJobLibrary(100)
      .then((items) => {
        if (!cancelled) {
          setJobLibrary(items)
        }
      })
      .catch((libraryError) => {
        if (!cancelled) {
          setError((libraryError as Error).message)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(
    () => () => {
      if (inputPreviewUrl) {
        URL.revokeObjectURL(inputPreviewUrl)
      }
    },
    [inputPreviewUrl]
  )

  useEffect(() => {
    if (!job || !isRunning) return
    const timer = window.setInterval(async () => {
      try {
        const updated = await fetchJob(job.id)
        setJob(updated)
        if (updated.status === 'completed') {
          await hydrateResults(updated.id)
          await refreshLibrary()
        }
      } catch (pollError) {
        setError((pollError as Error).message)
      }
    }, 1600)
    return () => window.clearInterval(timer)
  }, [job, isRunning])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!file) {
      setError(locale === 'ru' ? 'Сначала выберите видео файл.' : 'Pick a video file first.')
      return
    }
    setError(null)
    setIsSubmitting(true)
    setArtifacts([])
    setPeople([])
    setReport(null)
    setUploadProgress(null)
    try {
      let created: JobView
      try {
        created = await createJobChunked({
          file,
          language: language.trim() || undefined,
          autoDetectLanguage: language.trim().length === 0,
          qualityPreset,
          detectPeople: options.detectPeople,
          generateSummary: options.generateSummary,
          enableActiveSpeakerModel: options.enableActiveSpeakerModel,
          enableSubtitles: options.enableSubtitles,
          enableBurnedVideo: options.enableBurnedVideo,
          enableMaskOverlay: options.enableMaskOverlay,
          uiLocale: locale,
          exportFormats: selectedExportFormats,
          onProgress: (progress) => setUploadProgress(progress)
        })
      } catch {
        created = await createJob({
          file,
          language: language.trim() || undefined,
          autoDetectLanguage: language.trim().length === 0,
          qualityPreset,
          detectPeople: options.detectPeople,
          generateSummary: options.generateSummary,
          enableActiveSpeakerModel: options.enableActiveSpeakerModel,
          enableSubtitles: options.enableSubtitles,
          enableBurnedVideo: options.enableBurnedVideo,
          enableMaskOverlay: options.enableMaskOverlay,
          uiLocale: locale,
          exportFormats: selectedExportFormats
        })
      }
      setJob(created)
      setActiveTab('subtitles')
      setUploadProgress(null)
      await refreshLibrary()
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
      await refreshLibrary()
    } catch (bundleError) {
      setError((bundleError as Error).message)
    }
  }

  async function openJobFromLibrary(item: JobView) {
    setJob(item)
    setError(null)
    setUploadProgress(null)
    setArtifacts(item.artifacts ?? [])
    setPeople([])
    setReport(null)
    setActiveTab('subtitles')
    try {
      const latest = await fetchJob(item.id)
      setJob(latest)
      setArtifacts(latest.artifacts ?? item.artifacts ?? [])
      await hydrateResults(item.id)
    } catch (openError) {
      setError((openError as Error).message)
    }
  }

  function toggleFormat(format: string) {
    setSelectedExportFormats((current) =>
      current.includes(format) ? current.filter((item) => item !== format) : [...current, format]
    )
  }

  function toggleOption(key: keyof typeof options) {
    setOptions((current) => ({ ...current, [key]: !current[key] }))
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
          <div className="locale-switch">
            <button type="button" onClick={() => setLocale('ru')} className={locale === 'ru' ? 'on' : ''}>
              RU
            </button>
            <button type="button" onClick={() => setLocale('en')} className={locale === 'en' ? 'on' : ''}>
              EN
            </button>
          </div>
          <p className="eyebrow">NewtonSpect V1</p>
          <h1>{t.title}</h1>
          <p className="subtitle">{t.subtitle}</p>
        </div>
        <div className="orb-grid" aria-hidden>
          <span />
          <span />
          <span />
        </div>
      </header>

      <section className="workspace">
        <article className="panel upload-panel">
          <h2>{t.upload}</h2>
          <form onSubmit={onSubmit} className="upload-form">
            <label className="field">
              <span>{t.file}</span>
              <input
                type="file"
                accept="video/*"
                capture="environment"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <p className="mobile-hint">{t.mobileHint}</p>
            {inputPreviewUrl && (
              <div className="video-preview">
                <video className="video-player" src={inputPreviewUrl} controls playsInline preload="metadata" />
              </div>
            )}
            {uploadProgress && (
              <div className="upload-runtime">
                <div className="step-main">
                  <span>{locale === 'ru' ? 'Загрузка видео' : 'Video upload'}</span>
                  <span>
                    {uploadProgress.percent == null
                      ? '-'
                      : `${Math.round(uploadProgress.percent * 100)}%`}
                  </span>
                </div>
                <div className="progress-shell thin">
                  <div
                    className="progress-value"
                    style={{ width: `${Math.round((uploadProgress.percent ?? 0) * 100)}%` }}
                  />
                </div>
                <small>
                  {t.speed}: {formatBytesPerSecond(uploadProgress.speedBytesPerSec)} | {t.eta}:{' '}
                  {formatSeconds(uploadProgress.etaSeconds)}
                </small>
              </div>
            )}

            <label className="field">
              <span>{t.language}</span>
              <input
                type="text"
                placeholder="ru, en, de ..."
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              />
            </label>

            <label className="field">
              <span>{t.quality}</span>
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

            <div className="field">
              <span>{t.features}</span>
              <div className="toggle-grid">
                <label>
                  <input type="checkbox" checked={options.detectPeople} onChange={() => toggleOption('detectPeople')} />
                  {t.vision}
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={options.enableActiveSpeakerModel}
                    onChange={() => toggleOption('enableActiveSpeakerModel')}
                  />
                  {t.speaker}
                </label>
                <label>
                  <input type="checkbox" checked={options.generateSummary} onChange={() => toggleOption('generateSummary')} />
                  {t.summary}
                </label>
                <label>
                  <input type="checkbox" checked={options.enableSubtitles} onChange={() => toggleOption('enableSubtitles')} />
                  {t.subtitles}
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={options.enableBurnedVideo}
                    onChange={() => toggleOption('enableBurnedVideo')}
                  />
                  {t.burned}
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={options.enableMaskOverlay}
                    onChange={() => toggleOption('enableMaskOverlay')}
                  />
                  {t.mask}
                </label>
              </div>
            </div>

            <div className="field">
              <span>{t.formats}</span>
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
            </div>

            <button className="primary" type="submit" disabled={isSubmitting}>
              {isSubmitting ? t.creating : t.start}
            </button>
          </form>
        </article>

        <article className="panel progress-panel">
          <h2>{t.pipeline}</h2>
          <p className="status">{currentStageLabel(job, locale, t)}</p>
          <div className="progress-shell">
            <div
              className={job?.status === 'failed' ? 'progress-value failed' : 'progress-value'}
              style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }}
            />
          </div>
          <div className="runtime-cards">
            <div>
              <strong>{t.speed}</strong>
              <span>{formatSpeed(job?.runtime.current_speed, job?.runtime.current_speed_unit)}</span>
            </div>
            <div>
              <strong>{t.eta}</strong>
              <span>{formatSeconds(job?.runtime.overall_eta_seconds)}</span>
            </div>
          </div>
          <div className="step-list">
            {!job && (
              <div className="step">
                <div className="step-main">
                  <span>{locale === 'ru' ? 'Ожидание задачи' : 'Waiting for job'}</span>
                  <span>0%</span>
                </div>
                <small>{t.waiting}</small>
              </div>
            )}
            {visibleStages.map((step) => {
              const stage = job?.runtime.stages?.[step]
              const label = STEP_LABELS[step]?.[locale] ?? step
              const active = job?.current_step === step && job.status === 'running'
              const done = stage?.completed || job?.status === 'completed'
              const waitingText = locale === 'ru' ? 'Ожидание этапа' : 'Waiting for stage'
              const runningText = locale === 'ru' ? 'Этап выполняется' : 'Stage is running'
              const completedText = locale === 'ru' ? 'Этап завершен' : 'Stage completed'
              const stageMessage = stage?.message || (done ? completedText : active ? runningText : waitingText)
              return (
                <div key={step} className={done ? 'step done' : active ? 'step active' : 'step'}>
                  <div className="step-main">
                    <span>{label}</span>
                    <span>{stage ? `${Math.round(stage.progress * 100)}%` : '0%'}</span>
                  </div>
                  <small>{stageMessage}</small>
                  {(stage?.speed != null || stage?.eta_seconds != null) && (
                    <small>
                      {t.speed}: {formatSpeed(stage.speed, stage.speed_unit)} | {t.eta}:{' '}
                      {formatSeconds(stage.eta_seconds)}
                    </small>
                  )}
                </div>
              )
            })}
          </div>
        </article>
      </section>

      {error && <p className="error-banner">{error}</p>}

      <section className="panel storage-panel">
        <h2>{t.storage}</h2>
        <div className="library-grid">
          {jobLibrary.length === 0 && <p>{t.noLibrary}</p>}
          {jobLibrary.map((item) => (
            <article className="library-card" key={item.id}>
              <h3>
                {t.job} {item.id.slice(0, 8)}
              </h3>
              <p>{item.original_filename}</p>
              <p>
                {t.status}: {item.status}
              </p>
              <p>
                {t.deviceAuthor}: {item.created_by_device}
              </p>
              <p>
                {t.runtime}: {Math.round(item.progress * 100)}%
              </p>
              <button type="button" className="secondary" onClick={() => openJobFromLibrary(item)}>
                {t.open}
              </button>
            </article>
          ))}
        </div>
      </section>

      {job && (
        <section className="panel result-panel">
          <header className="result-header">
            <div>
              <h2>
                {t.job} {job.id.slice(0, 8)}
              </h2>
              <p className="job-meta">
                {job.original_filename} | {Math.round(job.progress * 100)}% | {job.status}
              </p>
              <p className="job-meta">
                {t.deviceAuthor}: {job.created_by_device}
              </p>
            </div>
            {completed && (
              <button className="secondary" type="button" onClick={handleBundleExport}>
                {t.downloadBundle}
              </button>
            )}
          </header>

          <div className="tab-row">
            {[
              ['subtitles', locale === 'ru' ? 'Субтитры' : 'Subtitles'],
              ['video', locale === 'ru' ? 'Видео' : 'Video Export'],
              ['people', locale === 'ru' ? 'Люди' : 'People'],
              ['summary', locale === 'ru' ? 'Конспект' : 'AI Summary']
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
                  target="_blank"
                  rel="noreferrer"
                >
                  <strong>{artifact.name}</strong>
                  <span>{artifact.kind}</span>
                </a>
              ))}
              {subtitleArtifacts.length === 0 && <p>{locale === 'ru' ? 'Файлы пока не готовы.' : 'No subtitle files yet.'}</p>}
            </div>
          )}

          {activeTab === 'video' && (
            <div className="tab-content">
              {videoPlaybackUrl && (
                <div className="video-preview">
                  <video
                    className="video-player"
                    src={videoPlaybackUrl}
                    controls
                    playsInline
                    preload="metadata"
                  />
                </div>
              )}
              {burnedArtifact ? (
                <a
                  className="primary ghost"
                  href={artifactDownloadUrl(job.id, burnedArtifact.name)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {locale === 'ru' ? 'Скачать видео' : 'Download video'}
                </a>
              ) : (
                <p>{locale === 'ru' ? 'Видео пока не готово.' : 'Video export is not ready yet.'}</p>
              )}
              {zipArtifact && (
                <a
                  className="primary ghost"
                  href={artifactDownloadUrl(job.id, zipArtifact.name)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {locale === 'ru' ? 'Скачать ZIP' : 'Download ZIP'}
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
                  <p>{locale === 'ru' ? 'В кадре' : 'Screen'}: {formatSeconds(person.track_stats.screen_time_seconds)}</p>
                  <p>{locale === 'ru' ? 'Говорит' : 'Speaking'}: {formatSeconds(person.track_stats.speaking_seconds)}</p>
                  <p>{locale === 'ru' ? 'Точность' : 'Confidence'}: {person.track_stats.avg_confidence.toFixed(3)}</p>
                  <ul>
                    {(person.key_comments.length ? person.key_comments : [locale === 'ru' ? 'Комментариев пока нет.' : 'No extracted comments yet.']).map(
                      (comment, idx) => (
                        <li key={idx}>{comment}</li>
                      )
                    )}
                  </ul>
                </article>
              ))}
              {people.length === 0 && <p>{locale === 'ru' ? 'Блок людей пока не готов.' : 'People analysis is not ready yet.'}</p>}
            </div>
          )}

          {activeTab === 'summary' && (
            <div className="tab-content summary">
              {report ? (
                <>
                  <section>
                    <h3>{locale === 'ru' ? 'Описание' : 'Summary'}</h3>
                    <pre>{report.summary_md || (locale === 'ru' ? 'Пусто.' : 'Summary is empty.')}</pre>
                  </section>
                  <section>
                    <h3>{locale === 'ru' ? 'Темы' : 'Topics'}</h3>
                    <ul>
                      {report.key_topics.map((topic) => (
                        <li key={topic}>{topic}</li>
                      ))}
                      {report.key_topics.length === 0 && <li>{locale === 'ru' ? 'Темы не выделены.' : 'No topics extracted.'}</li>}
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
                      {report.latex_blocks.length === 0 && <li>{locale === 'ru' ? 'Формул нет.' : 'No formulas detected.'}</li>}
                    </ul>
                  </section>
                </>
              ) : (
                <p>{locale === 'ru' ? 'Конспект пока не готов.' : 'AI summary is not ready yet.'}</p>
              )}
            </div>
          )}
        </section>
      )}
    </main>
  )
}

export default App
