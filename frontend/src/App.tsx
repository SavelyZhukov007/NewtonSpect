import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  artifactDownloadUrl,
  createJobChunked,
  downloadArtifact,
  fetchArtifactBlob,
  fetchArtifactText,
  fetchArtifacts,
  fetchFormatCapabilities,
  fetchJob,
  fetchJobLibrary,
  fetchPeople,
  fetchReport,
  jobUploadWebSocketUrl,
  requestExportBundle,
  type UploadProgress
} from './api'
import type {
  Artifact,
  FormatCapabilitiesResponse,
  JobView,
  PersonProfile,
  StageRuntime,
  VideoReport
} from './types'

type Locale = 'ru' | 'en'
type ResultTab = 'subtitles' | 'video' | 'people' | 'summary' | 'storage'
type QualityPreset = 'max_quality' | 'balanced' | 'max_speed'
type StreamingMode = 'dual_pass_hq' | 'final_only_hq' | 'live_only_fast'
type SubtitleEmbedMode = 'auto' | 'embedded' | 'sidecar' | 'burned'

interface SubtitleCue {
  start: number
  end: number
  text: string
}

interface SubtitleStyleState {
  fontSize: number
  color: string
  outlineColor: string
  backgroundColor: string
  positionPercent: number
}

interface CameraWsContext {
  ws: WebSocket
  completion: Promise<JobView>
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
] as const

const STAGE_LABELS: Record<Locale, Record<string, string>> = {
  ru: {
    ingest: 'Подготовка',
    audio_extract: 'Извлечение аудио',
    asr: 'Распознавание речи (Whisper)',
    subtitle_postprocess: 'Сборка субтитров',
    vision: 'Vision: люди и лица',
    speaker_attribution: 'Привязка говорящих',
    report: 'Конспект и summary',
    burned_video: 'Экспорт видео',
    mask_overlay: 'Наложение масок',
    done: 'Готово'
  },
  en: {
    ingest: 'Ingest',
    audio_extract: 'Audio extraction',
    asr: 'Speech recognition (Whisper)',
    subtitle_postprocess: 'Subtitle postprocess',
    vision: 'Vision: people and faces',
    speaker_attribution: 'Speaker attribution',
    report: 'Summary/report generation',
    burned_video: 'Video export',
    mask_overlay: 'Mask overlay rendering',
    done: 'Done'
  }
}

const QUALITY_OPTIONS: Array<{ value: QualityPreset; ru: string; en: string }> = [
  { value: 'max_quality', ru: 'Максимальное качество', en: 'Maximum quality' },
  { value: 'balanced', ru: 'Баланс', en: 'Balanced' },
  { value: 'max_speed', ru: 'Максимальная скорость', en: 'Maximum speed' }
]

const STREAMING_OPTIONS: Array<{ value: StreamingMode; ru: string; en: string }> = [
  { value: 'dual_pass_hq', ru: 'Dual-pass HQ (рекомендуется)', en: 'Dual-pass HQ (recommended)' },
  { value: 'final_only_hq', ru: 'Только финальный HQ', en: 'Final pass HQ only' },
  { value: 'live_only_fast', ru: 'Только live fast', en: 'Live fast only' }
]

const SUBTITLE_MODE_OPTIONS: Array<{ value: SubtitleEmbedMode; ru: string; en: string }> = [
  { value: 'auto', ru: 'Авто', en: 'Auto' },
  { value: 'embedded', ru: 'Вшить', en: 'Embedded' },
  { value: 'sidecar', ru: 'Отдельным файлом', en: 'Sidecar' },
  { value: 'burned', ru: 'Хардсаб (burned)', en: 'Burned-in' }
]

const TAB_ORDER: ResultTab[] = ['subtitles', 'video', 'people', 'summary', 'storage']

const TAB_LABELS: Record<Locale, Record<ResultTab, string>> = {
  ru: {
    subtitles: 'Subtitles',
    video: 'Video Export',
    people: 'People',
    summary: 'AI Summary',
    storage: 'Storage'
  },
  en: {
    subtitles: 'Subtitles',
    video: 'Video Export',
    people: 'People',
    summary: 'AI Summary',
    storage: 'Storage'
  }
}

function tx(locale: Locale, ru: string, en: string): string {
  return locale === 'ru' ? ru : en
}

function formatPercent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

function formatDuration(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) {
    return '--'
  }
  const rounded = Math.max(0, Math.round(seconds))
  const h = Math.floor(rounded / 3600)
  const m = Math.floor((rounded % 3600) / 60)
  const s = rounded % 60
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatBytes(bytes: number | null): string {
  if (bytes == null || !Number.isFinite(bytes)) {
    return '--'
  }
  if (bytes < 1024) return `${bytes.toFixed(0)} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatSpeed(value: number | null, unit: string | null): string {
  if (value == null || !Number.isFinite(value)) {
    return '--'
  }
  const rendered = value >= 100 ? value.toFixed(0) : value.toFixed(2)
  return unit ? `${rendered} ${unit}` : rendered
}

function parseTimeToken(value: string): number {
  const token = value.replace(',', '.').trim()
  const pieces = token.split(':')
  if (pieces.length < 2 || pieces.length > 3) {
    return 0
  }
  const sec = Number(pieces.at(-1) ?? 0)
  const min = Number(pieces.at(-2) ?? 0)
  const hour = pieces.length === 3 ? Number(pieces[0]) : 0
  if ([hour, min, sec].some((item) => Number.isNaN(item))) {
    return 0
  }
  return hour * 3600 + min * 60 + sec
}

function parseSrt(text: string): SubtitleCue[] {
  const normalized = text.replace(/\r/g, '')
  const blocks = normalized.split('\n\n')
  const cues: SubtitleCue[] = []
  for (const block of blocks) {
    const lines = block.split('\n').map((line) => line.trim())
    if (lines.length < 2) continue
    const timeLine = lines[1].includes('-->') ? lines[1] : lines[0]
    if (!timeLine.includes('-->')) continue
    const [startRaw, endRaw] = timeLine.split('-->').map((item) => item.trim())
    const payload = lines.slice(lines[1].includes('-->') ? 2 : 1).join(' ').trim()
    if (!payload) continue
    cues.push({
      start: parseTimeToken(startRaw),
      end: parseTimeToken(endRaw),
      text: payload
    })
  }
  return cues
}

function parseVtt(text: string): SubtitleCue[] {
  const normalized = text.replace(/\r/g, '')
  const blocks = normalized.split('\n\n')
  const cues: SubtitleCue[] = []
  for (const block of blocks) {
    const lines = block
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    if (!lines.length) continue
    let timeIndex = 0
    if (!lines[0].includes('-->')) {
      timeIndex = 1
    }
    if (!lines[timeIndex] || !lines[timeIndex].includes('-->')) continue
    const [startRaw, endRaw] = lines[timeIndex].split('-->').map((item) => item.trim())
    const payload = lines.slice(timeIndex + 1).join(' ').trim()
    if (!payload) continue
    cues.push({
      start: parseTimeToken(startRaw),
      end: parseTimeToken(endRaw),
      text: payload
    })
  }
  return cues
}

function bestVideoArtifact(artifacts: Artifact[]): Artifact | null {
  const order = ['video_masked', 'video_subtitled', 'video_burned']
  for (const kind of order) {
    const match = artifacts.find((item) => item.kind === kind)
    if (match) return match
  }
  return artifacts.find((item) => item.mime_type.startsWith('video/')) ?? null
}

function bestSubtitleArtifact(artifacts: Artifact[]): Artifact | null {
  return (
    artifacts.find((item) => item.kind === 'subtitle_vtt') ??
    artifacts.find((item) => item.kind === 'subtitle_srt') ??
    artifacts.find((item) => item.kind === 'subtitle_ass') ??
    null
  )
}

function supportsShareFiles(): boolean {
  return typeof navigator !== 'undefined' && typeof navigator.share === 'function'
}

function pickRecorderMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return undefined
  }
  const candidates = [
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/mp4',
    'video/webm'
  ]
  for (const mimeType of candidates) {
    if (MediaRecorder.isTypeSupported(mimeType)) {
      return mimeType
    }
  }
  return undefined
}

function cuesFromArtifact(name: string, text: string): SubtitleCue[] {
  const lower = name.toLowerCase()
  if (lower.endsWith('.vtt')) {
    return parseVtt(text)
  }
  return parseSrt(text)
}

function basename(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || path
}

export default function App() {
  const [locale, setLocale] = useState<Locale>('ru')
  const [activeTab, setActiveTab] = useState<ResultTab>('subtitles')

  const [jobLibrary, setJobLibrary] = useState<JobView[]>([])
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobView | null>(null)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [people, setPeople] = useState<PersonProfile[]>([])
  const [report, setReport] = useState<VideoReport | null>(null)

  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)

  const [language, setLanguage] = useState('')
  const [autoDetectLanguage, setAutoDetectLanguage] = useState(true)
  const [qualityPreset, setQualityPreset] = useState<QualityPreset>('max_quality')
  const [detectPeople, setDetectPeople] = useState(true)
  const [generateSummary, setGenerateSummary] = useState(true)
  const [enableActiveSpeakerModel, setEnableActiveSpeakerModel] = useState(true)
  const [enableSubtitles, setEnableSubtitles] = useState(true)
  const [enableBurnedVideo, setEnableBurnedVideo] = useState(true)
  const [enableMaskOverlay, setEnableMaskOverlay] = useState(false)
  const [streamingMode, setStreamingMode] = useState<StreamingMode>('dual_pass_hq')
  const [cameraMode, setCameraMode] = useState(false)
  const [autoStopSeconds, setAutoStopSeconds] = useState(20)
  const [showFaceMaskPreview, setShowFaceMaskPreview] = useState(false)
  const [outputVideoFormat, setOutputVideoFormat] = useState('mp4')
  const [subtitleEmbedMode, setSubtitleEmbedMode] = useState<SubtitleEmbedMode>('auto')
  const [selectedExportFormats, setSelectedExportFormats] = useState<string[]>([
    'srt',
    'vtt',
    'ass',
    'mp4_burned'
  ])
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyleState>({
    fontSize: 36,
    color: '#ffffff',
    outlineColor: '#000000',
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    positionPercent: 8
  })

  const [formatCapabilities, setFormatCapabilities] = useState<FormatCapabilitiesResponse | null>(null)
  const [showAllMuxers, setShowAllMuxers] = useState(false)

  const [subtitleText, setSubtitleText] = useState('')
  const [activeCueText, setActiveCueText] = useState('')

  const [cameraPreviewUrl, setCameraPreviewUrl] = useState<string | null>(null)
  const [cameraStreamReady, setCameraStreamReady] = useState(false)
  const [cameraRecording, setCameraRecording] = useState(false)
  const [cameraSessionActive, setCameraSessionActive] = useState(false)
  const [cameraElapsed, setCameraElapsed] = useState(0)
  const [cameraStatus, setCameraStatus] = useState<string | null>(null)
  const [cameraUploadProgress, setCameraUploadProgress] = useState<UploadProgress | null>(null)

  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const cameraPreviewRef = useRef<HTMLVideoElement | null>(null)
  const cameraStreamRef = useRef<MediaStream | null>(null)
  const cameraRecorderRef = useRef<MediaRecorder | null>(null)
  const cameraWsRef = useRef<CameraWsContext | null>(null)
  const cameraChunksRef = useRef<Blob[]>([])
  const cameraTickerRef = useRef<number | null>(null)
  const cameraAutoStopRef = useRef<number | null>(null)

  const stageMap = useMemo(() => job?.runtime?.stages ?? {}, [job?.runtime?.stages])
  const activeStages = useMemo(
    () =>
      STAGE_ORDER.map((name) => {
        const runtime = stageMap[name] as StageRuntime | undefined
        return {
          name,
          runtime
        }
      }),
    [stageMap]
  )

  const currentVideoArtifact = useMemo(() => bestVideoArtifact(artifacts), [artifacts])
  const currentSubtitleArtifact = useMemo(() => bestSubtitleArtifact(artifacts), [artifacts])
  const selectedFilePreviewUrl = useMemo(
    () => (selectedFile ? URL.createObjectURL(selectedFile) : null),
    [selectedFile]
  )

  const subtitleCues = useMemo(() => {
    if (!currentSubtitleArtifact || !subtitleText) return []
    return cuesFromArtifact(currentSubtitleArtifact.name, subtitleText)
  }, [currentSubtitleArtifact, subtitleText])

  const visibleFormatOptions = useMemo(() => {
    const curated = formatCapabilities?.curated ?? []
    const all = formatCapabilities?.all_muxers ?? []
    if (!showAllMuxers) {
      return curated.map((item) => item.format)
    }
    const merged = [...curated.map((item) => item.format), ...all.map((item) => item.format)]
    return [...new Set(merged)]
  }, [formatCapabilities, showAllMuxers])

  const loadLibrary = useCallback(async () => {
    const items = await fetchJobLibrary(120)
    setJobLibrary(items)
  }, [])

  const hydrateJobMaterials = useCallback(async (jobId: string) => {
    const [freshJob, freshArtifacts, freshPeople, freshReport] = await Promise.all([
      fetchJob(jobId),
      fetchArtifacts(jobId),
      fetchPeople(jobId),
      fetchReport(jobId)
    ])
    setJob(freshJob)
    setArtifacts(freshArtifacts)
    setPeople(freshPeople)
    setReport(freshReport)
  }, [])

  const openJob = useCallback(
    async (jobId: string) => {
      setError(null)
      setInfo(null)
      setSelectedJobId(jobId)
      await hydrateJobMaterials(jobId)
      setActiveTab('video')
    },
    [hydrateJobMaterials]
  )

  const resetCameraTimers = useCallback(() => {
    if (cameraTickerRef.current != null) {
      window.clearInterval(cameraTickerRef.current)
      cameraTickerRef.current = null
    }
    if (cameraAutoStopRef.current != null) {
      window.clearTimeout(cameraAutoStopRef.current)
      cameraAutoStopRef.current = null
    }
  }, [])

  const closeCamera = useCallback(() => {
    resetCameraTimers()
    if (cameraRecorderRef.current && cameraRecorderRef.current.state !== 'inactive') {
      cameraRecorderRef.current.stop()
    }
    if (cameraStreamRef.current) {
      cameraStreamRef.current.getTracks().forEach((track) => track.stop())
      cameraStreamRef.current = null
    }
    if (cameraPreviewRef.current) {
      cameraPreviewRef.current.srcObject = null
    }
    setCameraStreamReady(false)
    setCameraRecording(false)
    setCameraSessionActive(false)
    cameraWsRef.current = null
  }, [resetCameraTimers])

  useEffect(() => {
    void (async () => {
      try {
        await Promise.all([
          loadLibrary(),
          (async () => {
            const caps = await fetchFormatCapabilities()
            setFormatCapabilities(caps)
          })()
        ])
      } catch (err) {
        setError((err as Error).message)
      }
    })()
  }, [loadLibrary])

  useEffect(
    () => () => {
      if (selectedFilePreviewUrl) {
        URL.revokeObjectURL(selectedFilePreviewUrl)
      }
    },
    [selectedFilePreviewUrl]
  )

  useEffect(() => {
    if (!selectedJobId || !job) return
    if (job.status === 'completed' || job.status === 'failed') return

    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const nextJob = await fetchJob(selectedJobId)
          setJob(nextJob)
          setArtifacts(await fetchArtifacts(selectedJobId))
          if (nextJob.status === 'completed' || nextJob.status === 'failed') {
            setPeople(await fetchPeople(selectedJobId))
            setReport(await fetchReport(selectedJobId))
            await loadLibrary()
          }
        } catch (err) {
          setError((err as Error).message)
        }
      })()
    }, 1800)

    return () => window.clearInterval(timer)
  }, [selectedJobId, job, loadLibrary])

  useEffect(() => {
    if (!selectedJobId || !currentSubtitleArtifact) return
    void (async () => {
      try {
        const text = await fetchArtifactText(selectedJobId, currentSubtitleArtifact.name)
        setSubtitleText(text)
      } catch {
        setSubtitleText('')
      }
    })()
  }, [selectedJobId, currentSubtitleArtifact])

  useEffect(() => {
    return () => {
      resetCameraTimers()
      closeCamera()
      if (cameraPreviewUrl) {
        URL.revokeObjectURL(cameraPreviewUrl)
      }
    }
  }, [cameraPreviewUrl, closeCamera, resetCameraTimers])

  const clearRuntimeMessages = () => {
    setError(null)
    setInfo(null)
  }

  const collectCreateOptions = useCallback(
    () => ({
      language,
      autoDetectLanguage,
      qualityPreset,
      detectPeople,
      generateSummary,
      enableActiveSpeakerModel,
      enableSubtitles,
      enableBurnedVideo,
      enableMaskOverlay,
      uiLocale: locale,
      streamingMode,
      cameraMode,
      autoStopSeconds,
      showFaceMaskPreview,
      outputVideoFormat,
      subtitleEmbedMode,
      subtitleStyle: subtitleStyle as unknown as Record<string, unknown>,
      exportFormats: selectedExportFormats
    }),
    [
      language,
      autoDetectLanguage,
      qualityPreset,
      detectPeople,
      generateSummary,
      enableActiveSpeakerModel,
      enableSubtitles,
      enableBurnedVideo,
      enableMaskOverlay,
      locale,
      streamingMode,
      cameraMode,
      autoStopSeconds,
      showFaceMaskPreview,
      outputVideoFormat,
      subtitleEmbedMode,
      subtitleStyle,
      selectedExportFormats
    ]
  )

  const ensureCameraStream = useCallback(async () => {
    if (cameraStreamRef.current) {
      return cameraStreamRef.current
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user' },
      audio: true
    })
    cameraStreamRef.current = stream
    if (cameraPreviewRef.current) {
      cameraPreviewRef.current.srcObject = stream
      await cameraPreviewRef.current.play().catch(() => undefined)
    }
    setCameraStreamReady(true)
    return stream
  }, [])

  const startCameraUploadSession = useCallback(
    (filename: string): Promise<CameraWsContext> =>
      new Promise<CameraWsContext>((resolve, reject) => {
        const ws = new WebSocket(jobUploadWebSocketUrl())
        ws.binaryType = 'arraybuffer'

        const completion = new Promise<JobView>((resolveCompletion, rejectCompletion) => {
          ws.onmessage = (event) => {
            if (typeof event.data !== 'string') return
            let payload: Record<string, unknown>
            try {
              payload = JSON.parse(event.data) as Record<string, unknown>
            } catch {
              rejectCompletion(new Error('Invalid camera websocket payload'))
              return
            }
            const type = String(payload.type ?? '')
            if (type === 'progress') {
              setCameraUploadProgress({
                receivedBytes: Number(payload.received_bytes ?? 0),
                totalSize: payload.total_size == null ? null : Number(payload.total_size),
                percent: payload.percent == null ? null : Number(payload.percent),
                speedBytesPerSec:
                  payload.speed_bytes_per_sec == null ? null : Number(payload.speed_bytes_per_sec),
                etaSeconds: payload.eta_seconds == null ? null : Number(payload.eta_seconds)
              })
            }
            if (type === 'error') {
              rejectCompletion(new Error(String(payload.message ?? 'Upload failed')))
            }
            if (type === 'completed') {
              const created = payload.job as JobView | undefined
              if (!created) {
                rejectCompletion(new Error('Server returned completed without job'))
                return
              }
              resolveCompletion(created)
            }
          }
          ws.onerror = () => rejectCompletion(new Error('Camera upload websocket failed'))
          ws.onclose = () => undefined
        })

        ws.onopen = () => {
          ws.send(
            JSON.stringify({
              type: 'start',
              filename,
              total_size: null
            })
          )
        }

        const startedListener = (event: MessageEvent) => {
          if (typeof event.data !== 'string') return
          try {
            const payload = JSON.parse(event.data) as Record<string, unknown>
            if (payload.type === 'started') {
              ws.removeEventListener('message', startedListener)
              resolve({ ws, completion })
            }
          } catch {
            reject(new Error('Unable to start camera upload session'))
          }
        }

        ws.addEventListener('message', startedListener)
        ws.addEventListener('error', () => {
          reject(new Error('Unable to connect camera upload websocket'))
        })
      }),
    []
  )

  const handleStartCamera = useCallback(async () => {
    clearRuntimeMessages()
    try {
      await ensureCameraStream()
      setCameraStatus(
        tx(
          locale,
          'Камера активна. Перед записью представьтесь: "Меня зовут ...".',
          'Camera is ready. Introduce yourself before recording: "My name is ...".'
        )
      )
    } catch (err) {
      setError((err as Error).message)
    }
  }, [ensureCameraStream, locale])

  const handleStartRecording = useCallback(async () => {
    clearRuntimeMessages()
    setCameraMode(true)
    try {
      const stream = await ensureCameraStream()
      const mimeType = pickRecorderMimeType()
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
      cameraRecorderRef.current = recorder
      cameraChunksRef.current = []
      setCameraUploadProgress(null)

      const uploadSession = await startCameraUploadSession(
        `camera-${new Date().toISOString().replace(/[:.]/g, '-')}.webm`
      )
      cameraWsRef.current = uploadSession
      setCameraSessionActive(true)

      recorder.ondataavailable = (event: BlobEvent) => {
        if (!event.data || event.data.size <= 0) {
          return
        }
        cameraChunksRef.current.push(event.data)
        void (async () => {
          const buffer = await event.data.arrayBuffer()
          if (uploadSession.ws.readyState === WebSocket.OPEN) {
            uploadSession.ws.send(buffer)
          }
        })()
      }

      recorder.onstop = () => {
        resetCameraTimers()
      }

      recorder.start(1000)
      setCameraRecording(true)
      setCameraElapsed(0)
      setCameraStatus(tx(locale, 'Идет запись и потоковая отправка чанков...', 'Recording and streaming chunks...'))

      cameraTickerRef.current = window.setInterval(() => {
        setCameraElapsed((prev) => prev + 1)
      }, 1000)

      cameraAutoStopRef.current = window.setTimeout(() => {
        if (recorder.state !== 'inactive') {
          recorder.stop()
          setCameraRecording(false)
          setCameraStatus(
            tx(
              locale,
              'Автостоп 20+ сек. Запускаю финальный HQ-проход.',
              'Auto-stop reached (20+ sec). Running final HQ pass.'
            )
          )
        }
      }, Math.max(5, autoStopSeconds) * 1000)
    } catch (err) {
      setError((err as Error).message)
      closeCamera()
    }
  }, [autoStopSeconds, closeCamera, ensureCameraStream, locale, resetCameraTimers, startCameraUploadSession])

  const finalizeCameraJob = useCallback(async () => {
    const recorder = cameraRecorderRef.current
    const wsContext = cameraWsRef.current
    if (!recorder || !wsContext) {
      throw new Error('Camera recording session is not started')
    }

    if (recorder.state !== 'inactive') {
      recorder.stop()
    }
    setCameraRecording(false)
    setCameraStatus(tx(locale, 'Финализирую запись...', 'Finalizing recording...'))

    const cameraBlob = new Blob(cameraChunksRef.current, {
      type: recorder.mimeType || 'video/webm'
    })

    if (cameraPreviewUrl) {
      URL.revokeObjectURL(cameraPreviewUrl)
    }
    const previewUrl = URL.createObjectURL(cameraBlob)
    setCameraPreviewUrl(previewUrl)

    try {
      wsContext.ws.send(
        JSON.stringify({
          type: 'finish',
          options: {
            language: language || null,
            auto_detect_language: autoDetectLanguage,
            quality_preset: qualityPreset,
            export_formats: selectedExportFormats,
            detect_people: detectPeople,
            generate_summary: generateSummary,
            enable_active_speaker_model: enableActiveSpeakerModel,
            enable_subtitles: enableSubtitles,
            enable_burned_video: enableBurnedVideo,
            enable_mask_overlay: enableMaskOverlay,
            ui_locale: locale,
            streaming_mode: streamingMode,
            camera_mode: true,
            auto_stop_seconds: autoStopSeconds,
            show_face_mask_preview: showFaceMaskPreview,
            output_video_format: outputVideoFormat,
            subtitle_embed_mode: subtitleEmbedMode,
            subtitle_style: subtitleStyle
          }
        })
      )

      const createdJob = await wsContext.completion
      setJob(createdJob)
      setSelectedJobId(createdJob.id)
      setArtifacts(createdJob.artifacts)
      setPeople([])
      setReport(null)
      setActiveTab('video')
      setCameraStatus(tx(locale, 'Видео отправлено. Идет финальный HQ-анализ.', 'Uploaded. Running final HQ analysis.'))

      await loadLibrary()
    } finally {
      wsContext.ws.close()
      cameraWsRef.current = null
      setCameraSessionActive(false)
    }
  }, [
    autoDetectLanguage,
    autoStopSeconds,
    cameraPreviewUrl,
    detectPeople,
    enableActiveSpeakerModel,
    enableBurnedVideo,
    enableMaskOverlay,
    enableSubtitles,
    generateSummary,
    language,
    loadLibrary,
    locale,
    outputVideoFormat,
    qualityPreset,
    selectedExportFormats,
    showFaceMaskPreview,
    streamingMode,
    subtitleEmbedMode,
    subtitleStyle
  ])

  const handleStopRecording = useCallback(async () => {
    clearRuntimeMessages()
    try {
      await finalizeCameraJob()
    } catch (err) {
      setError((err as Error).message)
    }
  }, [finalizeCameraJob])

  const handleFileSubmit = useCallback(async () => {
    clearRuntimeMessages()
    if (!selectedFile) {
      setError(tx(locale, 'Выберите видео для загрузки.', 'Pick a video file first.'))
      return
    }
    setIsSubmitting(true)
    setUploadProgress(null)
    try {
      const created = await createJobChunked({
        file: selectedFile,
        ...collectCreateOptions(),
        onProgress: (progress) => {
          setUploadProgress(progress)
        }
      })
      setSelectedJobId(created.id)
      setJob(created)
      setArtifacts(created.artifacts)
      setPeople([])
      setReport(null)
      setActiveTab('video')
      setInfo(tx(locale, 'Видео загружено. Началась фоновая обработка.', 'Video uploaded. Background processing started.'))
      await loadLibrary()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setIsSubmitting(false)
    }
  }, [collectCreateOptions, loadLibrary, locale, selectedFile])

  const handleDownload = useCallback(
    async (artifact: Artifact) => {
      if (!selectedJobId) return
      clearRuntimeMessages()
      try {
        const filename = await downloadArtifact(selectedJobId, artifact.name)
        setInfo(tx(locale, `Скачивание запущено: ${filename}`, `Download started: ${filename}`))
      } catch (err) {
        setError((err as Error).message)
      }
    },
    [locale, selectedJobId]
  )

  const handleShare = useCallback(
    async (artifact: Artifact) => {
      if (!selectedJobId) return
      clearRuntimeMessages()
      try {
        const payload = await fetchArtifactBlob(selectedJobId, artifact.name)
        const file = new File([payload.blob], payload.filename, { type: payload.blob.type })
        if (supportsShareFiles()) {
          const canShare =
            typeof navigator.canShare === 'function' ? navigator.canShare({ files: [file] }) : true
          if (canShare) {
            await navigator.share({ files: [file], title: payload.filename })
            setInfo(tx(locale, 'Отправка файла через share выполнена.', 'Shared via system share dialog.'))
            return
          }
        }
        await handleDownload(artifact)
      } catch (err) {
        setError((err as Error).message)
      }
    },
    [handleDownload, locale, selectedJobId]
  )

  const handleBuildZip = useCallback(async () => {
    if (!selectedJobId) return
    clearRuntimeMessages()
    try {
      const updatedArtifacts = await requestExportBundle(selectedJobId, selectedExportFormats)
      setArtifacts(updatedArtifacts)
      setInfo(tx(locale, 'ZIP-пакет собран. Можно скачать в разделе Subtitles/Video.', 'ZIP bundle built. Download it from Subtitles/Video tabs.'))
      await loadLibrary()
    } catch (err) {
      setError((err as Error).message)
    }
  }, [loadLibrary, locale, selectedExportFormats, selectedJobId])

  const handleOpenLibraryJob = useCallback(
    async (jobId: string) => {
      try {
        await openJob(jobId)
      } catch (err) {
        setError((err as Error).message)
      }
    },
    [openJob]
  )

  const onVideoTimeUpdate = useCallback(() => {
    const time = videoRef.current?.currentTime ?? 0
    const cue = subtitleCues.find((item) => time >= item.start && time <= item.end)
    setActiveCueText(cue?.text ?? '')
  }, [subtitleCues])

  const runtimeCurrentStage = job?.current_step ? stageMap[job.current_step] : undefined

  const subtitleOverlayStyle = {
    color: subtitleStyle.color,
    fontSize: `${subtitleStyle.fontSize}px`,
    WebkitTextStroke: `1px ${subtitleStyle.outlineColor}`,
    textShadow: `0 2px 10px ${subtitleStyle.outlineColor}`,
    background: subtitleStyle.backgroundColor,
    bottom: `calc(${subtitleStyle.positionPercent}% + env(safe-area-inset-bottom))`
  } as const

  const humanUploadInfo = uploadProgress
    ? `${formatBytes(uploadProgress.receivedBytes)} / ${formatBytes(uploadProgress.totalSize)}`
    : '--'

  return (
    <main className="page">
      <section className="hero">
        <div className="hero-content">
          <div className="locale-switch">
            <button
              className={locale === 'ru' ? 'on' : ''}
              type="button"
              onClick={() => setLocale('ru')}
            >
              RU
            </button>
            <button
              className={locale === 'en' ? 'on' : ''}
              type="button"
              onClick={() => setLocale('en')}
            >
              EN
            </button>
          </div>
          <p className="eyebrow">AstraOrpheus</p>
          <h1>{tx(locale, 'Локальная платформа автосубтитров и видео-аналитики', 'Local subtitles and video analytics platform')}</h1>
          <p className="subtitle">
            {tx(
              locale,
              'Whisper large-v3 + OpenVINO + Ollama qwen2.5:3b. Реальный прогресс этапов, камера-режим и надежные загрузки/скачивания на desktop и mobile.',
              'Whisper large-v3 + OpenVINO + Ollama qwen2.5:3b. Real stage telemetry, camera mode, and reliable downloads on desktop and mobile.'
            )}
          </p>
        </div>
        <div className="orb-grid" aria-hidden="true">
          <img src="/astraorpheus-logo.svg" alt="AstraOrpheus logo" className="brand-logo" />
          <span />
          <span />
          <span />
        </div>
      </section>

      <section className="workspace">
        <div className="panel upload-panel">
          <h2>{tx(locale, 'Загрузка и настройки', 'Upload and settings')}</h2>
          <div className="upload-dropzone">
            <img src="/astraorpheus-logo.svg" alt="AstraOrpheus" />
            <p>{tx(locale, 'Перетащите видео или выберите файл', 'Drop a video or choose a file')}</p>
            <label className="file-picker">
              <input
                type="file"
                accept="video/*"
                onChange={(event) => {
                  const file = event.target.files?.[0] ?? null
                  setSelectedFile(file)
                }}
              />
              {tx(locale, 'Выбрать видео', 'Choose video')}
            </label>
          </div>

          {selectedFilePreviewUrl ? (
            <div className="video-preview">
              <video className="video-player" src={selectedFilePreviewUrl} controls playsInline />
            </div>
          ) : null}

          <div className="upload-form">
            <div className="field">
              <span>{tx(locale, 'Язык', 'Language')}</span>
              <input
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                placeholder={tx(locale, 'ru / en / auto', 'ru / en / auto')}
              />
            </div>

            <div className="field">
              <span>{tx(locale, 'Качество ASR', 'ASR quality')}</span>
              <select
                value={qualityPreset}
                onChange={(event) => setQualityPreset(event.target.value as QualityPreset)}
              >
                {QUALITY_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {locale === 'ru' ? item.ru : item.en}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <span>{tx(locale, 'Режим обработки', 'Processing mode')}</span>
              <select
                value={streamingMode}
                onChange={(event) => setStreamingMode(event.target.value as StreamingMode)}
              >
                {STREAMING_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {locale === 'ru' ? item.ru : item.en}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <span>{tx(locale, 'Формат выходного видео', 'Output video format')}</span>
              <select
                value={outputVideoFormat}
                onChange={(event) => setOutputVideoFormat(event.target.value)}
              >
                {visibleFormatOptions.map((fmt) => (
                  <option key={fmt} value={fmt}>
                    {fmt}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="secondary compact"
                onClick={() => setShowAllMuxers((prev) => !prev)}
              >
                {showAllMuxers
                  ? tx(locale, 'Скрыть все muxers', 'Hide all muxers')
                  : tx(locale, 'Показать все ffmpeg muxers', 'Show all ffmpeg muxers')}
              </button>
            </div>

            <div className="field">
              <span>{tx(locale, 'Режим субтитров', 'Subtitle embedding mode')}</span>
              <select
                value={subtitleEmbedMode}
                onChange={(event) => setSubtitleEmbedMode(event.target.value as SubtitleEmbedMode)}
              >
                {SUBTITLE_MODE_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {locale === 'ru' ? item.ru : item.en}
                  </option>
                ))}
              </select>
            </div>

            <div className="toggle-grid">
              <label>
                <input
                  type="checkbox"
                  checked={autoDetectLanguage}
                  onChange={(event) => setAutoDetectLanguage(event.target.checked)}
                />
                {tx(locale, 'Автоопределение языка', 'Auto-detect language')}
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={detectPeople}
                  onChange={(event) => setDetectPeople(event.target.checked)}
                />
                {tx(locale, 'Распознавать людей', 'Detect people')}
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={generateSummary}
                  onChange={(event) => setGenerateSummary(event.target.checked)}
                />
                {tx(locale, 'Генерировать AI-конспект', 'Generate AI summary')}
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableActiveSpeakerModel}
                  onChange={(event) => setEnableActiveSpeakerModel(event.target.checked)}
                />
                {tx(locale, 'ASD / определение говорящего', 'Active speaker detection')}
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableSubtitles}
                  onChange={(event) => setEnableSubtitles(event.target.checked)}
                />
                {tx(locale, 'Создавать субтитры', 'Generate subtitles')}
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableBurnedVideo}
                  onChange={(event) => setEnableBurnedVideo(event.target.checked)}
                />
                {tx(locale, 'Создавать видео-экспорт', 'Render video export')}
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={enableMaskOverlay}
                  onChange={(event) => setEnableMaskOverlay(event.target.checked)}
                />
                {tx(locale, 'С маской (OpenVINO overlay)', 'Mask overlay (OpenVINO)')}
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={showFaceMaskPreview}
                  onChange={(event) => setShowFaceMaskPreview(event.target.checked)}
                />
                {tx(locale, 'Показывать маску лица в превью', 'Show face mask in preview')}
              </label>
            </div>

            <div className="field">
              <span>{tx(locale, 'Автостоп записи (сек)', 'Camera auto-stop (sec)')}</span>
              <input
                type="number"
                min={5}
                max={120}
                value={autoStopSeconds}
                onChange={(event) => setAutoStopSeconds(Number(event.target.value || 20))}
              />
            </div>

            <div className="chips">
              {['srt', 'vtt', 'ass', 'mp4_burned', 'video_subtitled', 'video_masked'].map((fmt) => {
                const active = selectedExportFormats.includes(fmt)
                return (
                  <button
                    key={fmt}
                    type="button"
                    className={`chip ${active ? 'active' : ''}`}
                    onClick={() => {
                      setSelectedExportFormats((prev) => {
                        if (prev.includes(fmt)) {
                          return prev.filter((item) => item !== fmt)
                        }
                        return [...prev, fmt]
                      })
                    }}
                  >
                    {fmt}
                  </button>
                )
              })}
            </div>

            <button
              type="button"
              className="primary"
              disabled={isSubmitting || cameraRecording}
              onClick={() => void handleFileSubmit()}
            >
              {isSubmitting
                ? tx(locale, 'Загрузка...', 'Uploading...')
                : tx(locale, 'Запустить обработку файла', 'Start file processing')}
            </button>
          </div>

          <div className="upload-runtime">
            <strong>{tx(locale, 'Телеметрия загрузки', 'Upload telemetry')}</strong>
            <div className="progress-shell thin">
              <div
                className="progress-value"
                style={{ width: uploadProgress?.percent != null ? formatPercent(uploadProgress.percent) : '0%' }}
              />
            </div>
            <div className="upload-stats">
              <span>{tx(locale, 'Данные', 'Data')}: {humanUploadInfo}</span>
              <span>
                {tx(locale, 'Скорость', 'Speed')}:{' '}
                {uploadProgress?.speedBytesPerSec == null
                  ? '--'
                  : `${formatBytes(uploadProgress.speedBytesPerSec)}/s`}
              </span>
              <span>{tx(locale, 'ETA', 'ETA')}: {formatDuration(uploadProgress?.etaSeconds ?? null)}</span>
            </div>
          </div>
        </div>

        <div className="panel">
          <h2>{tx(locale, 'Camera Studio + Прогресс', 'Camera Studio + Progress')}</h2>

          <div className="camera-studio">
            <div className="camera-circle-wrap">
              <video ref={cameraPreviewRef} className="camera-circle" autoPlay playsInline muted />
              {showFaceMaskPreview ? <div className="camera-mask" /> : null}
              {!cameraStreamReady ? <div className="camera-placeholder">Astra</div> : null}
            </div>
            <div className="camera-actions">
              <button type="button" className="secondary" onClick={() => void handleStartCamera()}>
                {tx(locale, 'Включить камеру', 'Enable camera')}
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => void handleStartRecording()}
                disabled={cameraRecording}
              >
                {tx(locale, 'Начать запись', 'Start recording')}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => void handleStopRecording()}
                disabled={!cameraSessionActive}
              >
                {tx(locale, 'Стоп и отправить', 'Stop and send')}
              </button>
              <button type="button" className="secondary" onClick={closeCamera}>
                {tx(locale, 'Отключить камеру', 'Disable camera')}
              </button>
            </div>
          </div>

          <p className="mobile-hint">
            {tx(
              locale,
              'Поддерживается iPhone/Android: запись, загрузка чанков по WebSocket и дальнейшая фоновая обработка.',
              'iPhone/Android supported: recording, chunked WebSocket upload, and background processing.'
            )}
          </p>

          <div className="runtime-cards">
            <div>
              <strong>{tx(locale, 'Статус', 'Status')}</strong>
              <span>{job ? `${job.status} / ${formatPercent(job.progress)}` : '--'}</span>
            </div>
            <div>
              <strong>{tx(locale, 'Текущий шаг', 'Current stage')}</strong>
              <span>
                {job?.current_step
                  ? STAGE_LABELS[locale][job.current_step] ?? job.current_step
                  : '--'}
              </span>
            </div>
            <div>
              <strong>{tx(locale, 'Скорость этапа', 'Stage speed')}</strong>
              <span>{formatSpeed(runtimeCurrentStage?.speed ?? null, runtimeCurrentStage?.speed_unit ?? null)}</span>
            </div>
            <div>
              <strong>{tx(locale, 'ETA', 'ETA')}</strong>
              <span>{formatDuration(runtimeCurrentStage?.eta_seconds ?? null)}</span>
            </div>
          </div>

          {cameraRecording || cameraUploadProgress ? (
            <div className="upload-runtime">
              <strong>{tx(locale, 'Camera streaming runtime', 'Camera streaming runtime')}</strong>
              <div className="progress-shell thin">
                <div
                  className="progress-value"
                  style={{
                    width:
                      cameraUploadProgress?.percent != null
                        ? formatPercent(cameraUploadProgress.percent)
                        : `${Math.min(95, (cameraElapsed / Math.max(autoStopSeconds, 1)) * 100)}%`
                  }}
                />
              </div>
              <div className="upload-stats">
                <span>{tx(locale, 'Длительность', 'Duration')}: {formatDuration(cameraElapsed)}</span>
                <span>
                  {tx(locale, 'Передано', 'Uploaded')}:{' '}
                  {formatBytes(cameraUploadProgress?.receivedBytes ?? null)}
                </span>
                <span>{tx(locale, 'ETA', 'ETA')}: {formatDuration(cameraUploadProgress?.etaSeconds ?? null)}</span>
              </div>
            </div>
          ) : null}

          {cameraPreviewUrl ? (
            <div className="video-preview">
              <video className="video-player" src={cameraPreviewUrl} controls playsInline />
            </div>
          ) : null}

          {cameraStatus ? <p className="status-note">{cameraStatus}</p> : null}

          <p className="status">
            {job
              ? tx(locale, `Прогресс задачи: ${formatPercent(job.progress)}`, `Job progress: ${formatPercent(job.progress)}`)
              : tx(locale, 'Задача еще не создана.', 'No job created yet.')}
          </p>

          <div className="progress-shell">
            <div
              className={`progress-value ${job?.status === 'failed' ? 'failed' : ''}`}
              style={{ width: job ? formatPercent(job.progress) : '0%' }}
            />
          </div>

          <div className="step-list">
            {activeStages.map(({ name, runtime }) => {
              const isActive = job?.current_step === name && job.status === 'running'
              const isDone = runtime?.completed || name === 'done' && job?.status === 'completed'
              return (
                <div
                  key={name}
                  className={`step ${isActive ? 'active' : ''} ${isDone ? 'done' : ''}`}
                >
                  <div className="step-main">
                    <strong>{STAGE_LABELS[locale][name]}</strong>
                    <span>{runtime ? formatPercent(runtime.progress) : '--'}</span>
                  </div>
                  <small>
                    {runtime?.message ||
                      (isActive
                        ? tx(locale, 'Этап выполняется...', 'Running...')
                        : tx(locale, 'Ожидание этапа...', 'Waiting...'))}
                  </small>
                  <small>
                    {tx(locale, 'Скорость', 'Speed')}: {formatSpeed(runtime?.speed ?? null, runtime?.speed_unit ?? null)}
                    {' | '}
                    ETA: {formatDuration(runtime?.eta_seconds ?? null)}
                  </small>
                </div>
              )
            })}
          </div>

          {job?.error_message ? <p className="error-banner">{job.error_message}</p> : null}
          {error ? <p className="error-banner">{error}</p> : null}
          {info ? <p className="info-banner">{info}</p> : null}
        </div>
      </section>

      <section className="panel result-panel">
        <div className="result-header">
          <div>
            <h2>{tx(locale, 'Результаты', 'Results')}</h2>
            {job ? (
              <p className="job-meta">
                {job.original_filename} · {job.created_by_device} · {formatDate(job.created_at)}
              </p>
            ) : (
              <p className="job-meta">{tx(locale, 'Нет выбранной задачи', 'No selected job')}</p>
            )}
          </div>
          <button type="button" className="secondary" onClick={() => void handleBuildZip()} disabled={!selectedJobId}>
            {tx(locale, 'Собрать ZIP пакет', 'Build ZIP bundle')}
          </button>
        </div>

        <div className="tab-row">
          {TAB_ORDER.map((tab) => (
            <button
              key={tab}
              type="button"
              className={`tab ${activeTab === tab ? 'active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {TAB_LABELS[locale][tab]}
            </button>
          ))}
        </div>

        <div className="tab-content">
          {activeTab === 'subtitles' ? (
            <div className="grid-list">
              {artifacts.length === 0 ? <p>{tx(locale, 'Материалы пока не готовы.', 'Artifacts are not ready yet.')}</p> : null}
              {artifacts
                .filter((item) =>
                  ['subtitle_srt', 'subtitle_vtt', 'subtitle_ass', 'report_markdown', 'report_json'].includes(item.kind)
                )
                .map((artifact) => (
                  <div key={artifact.name} className="artifact-row">
                    <a className="artifact-link" href={artifactDownloadUrl(selectedJobId ?? '', artifact.name)}>
                      <strong>{artifact.name}</strong>
                      <span>{artifact.kind}</span>
                    </a>
                    <div className="artifact-actions">
                      <button type="button" className="secondary compact" onClick={() => void handleDownload(artifact)}>
                        {tx(locale, 'Скачать', 'Download')}
                      </button>
                      <button type="button" className="secondary compact" onClick={() => void handleShare(artifact)}>
                        {tx(locale, 'Поделиться', 'Share')}
                      </button>
                    </div>
                  </div>
                ))}
              {subtitleCues.length > 0 ? (
                <div className="subtitle-preview">
                  <h3>{tx(locale, 'Превью субтитров', 'Subtitle preview')}</h3>
                  <p>{subtitleCues.slice(0, 5).map((cue) => cue.text).join(' · ')}</p>
                </div>
              ) : null}
            </div>
          ) : null}

          {activeTab === 'video' ? (
            <div className="grid-list">
              {!currentVideoArtifact ? (
                <p>{tx(locale, 'Видео-экспорт еще не готов.', 'Video export is not ready yet.')}</p>
              ) : (
                <>
                  <div className="custom-player">
                    <video
                      ref={videoRef}
                      className="video-player"
                      src={artifactDownloadUrl(selectedJobId ?? '', currentVideoArtifact.name)}
                      controls
                      playsInline
                      onTimeUpdate={onVideoTimeUpdate}
                    />
                    {activeCueText ? (
                      <div className="subtitle-overlay" style={subtitleOverlayStyle}>
                        {subtitleCues.length > 0 ? activeCueText : ''}
                      </div>
                    ) : null}
                  </div>

                  <div className="subtitle-controls">
                    <h3>{tx(locale, 'Стиль субтитров (live preview)', 'Subtitle style (live preview)')}</h3>
                    <div className="style-grid">
                      <label>
                        {tx(locale, 'Размер', 'Size')}
                        <input
                          type="number"
                          min={16}
                          max={72}
                          value={subtitleStyle.fontSize}
                          onChange={(event) =>
                            setSubtitleStyle((prev) => ({ ...prev, fontSize: Number(event.target.value) }))
                          }
                        />
                      </label>
                      <label>
                        {tx(locale, 'Цвет', 'Color')}
                        <input
                          type="color"
                          value={subtitleStyle.color}
                          onChange={(event) =>
                            setSubtitleStyle((prev) => ({ ...prev, color: event.target.value }))
                          }
                        />
                      </label>
                      <label>
                        {tx(locale, 'Обводка', 'Outline')}
                        <input
                          type="color"
                          value={subtitleStyle.outlineColor}
                          onChange={(event) =>
                            setSubtitleStyle((prev) => ({ ...prev, outlineColor: event.target.value }))
                          }
                        />
                      </label>
                      <label>
                        {tx(locale, 'Позиция (%)', 'Position (%)')}
                        <input
                          type="range"
                          min={2}
                          max={24}
                          value={subtitleStyle.positionPercent}
                          onChange={(event) =>
                            setSubtitleStyle((prev) => ({
                              ...prev,
                              positionPercent: Number(event.target.value)
                            }))
                          }
                        />
                      </label>
                    </div>
                  </div>
                </>
              )}

              {artifacts
                .filter((item) => item.mime_type.startsWith('video/') || item.name.endsWith('.zip'))
                .map((artifact) => (
                  <div key={artifact.name} className="artifact-row">
                    <a className="artifact-link" href={artifactDownloadUrl(selectedJobId ?? '', artifact.name)}>
                      <strong>{artifact.name}</strong>
                      <span>{artifact.kind}</span>
                    </a>
                    <div className="artifact-actions">
                      <button type="button" className="secondary compact" onClick={() => void handleDownload(artifact)}>
                        {tx(locale, 'Скачать', 'Download')}
                      </button>
                      <button type="button" className="secondary compact" onClick={() => void handleShare(artifact)}>
                        {tx(locale, 'Поделиться', 'Share')}
                      </button>
                    </div>
                  </div>
                ))}
            </div>
          ) : null}

          {activeTab === 'people' ? (
            <div className="people-grid">
              {people.length === 0 ? <p>{tx(locale, 'Люди пока не найдены.', 'No people detected yet.')}</p> : null}
              {people.map((person) => (
                <article key={person.person_id} className="person-card">
                  {person.portrait_path ? (
                    <img src={artifactDownloadUrl(selectedJobId ?? '', basename(person.portrait_path))} alt={person.display_name ?? person.person_id} />
                  ) : (
                    <div className="portrait-fallback">{person.display_name ?? person.person_id}</div>
                  )}
                  <h3>{person.display_name ?? person.person_id}</h3>
                  <p>
                    {tx(locale, 'Экранное время', 'Screen time')}: {person.track_stats.screen_time_seconds.toFixed(1)}s
                  </p>
                  <p>
                    {tx(locale, 'Говорил', 'Speaking')}: {person.track_stats.speaking_seconds.toFixed(1)}s
                  </p>
                  <p>
                    {tx(locale, 'Confidence', 'Confidence')}: {(person.track_stats.avg_confidence * 100).toFixed(1)}%
                  </p>
                  {person.key_comments.length > 0 ? (
                    <ul>
                      {person.key_comments.map((comment, index) => (
                        <li key={`${person.person_id}-${index}`}>{comment}</li>
                      ))}
                    </ul>
                  ) : null}
                </article>
              ))}
            </div>
          ) : null}

          {activeTab === 'summary' ? (
            <div className="summary">
              {!report ? <p>{tx(locale, 'Конспект еще не готов.', 'Summary is not ready yet.')}</p> : null}
              {report ? (
                <>
                  <section>
                    <h3>{tx(locale, 'Краткое описание', 'Summary')}</h3>
                    <pre>{report.summary_md}</pre>
                  </section>
                  <section>
                    <h3>{tx(locale, 'Темы', 'Topics')}</h3>
                    <ul>
                      {report.key_topics.map((topic, index) => (
                        <li key={`topic-${index}`}>{topic}</li>
                      ))}
                    </ul>
                  </section>
                  <section>
                    <h3>{tx(locale, 'LaTeX', 'LaTeX blocks')}</h3>
                    {report.latex_blocks.length === 0 ? (
                      <p>{tx(locale, 'Формулы не обнаружены.', 'No formulas found.')}</p>
                    ) : (
                      report.latex_blocks.map((block, index) => (
                        <pre key={`latex-${index}`}>{`$$${block}$$`}</pre>
                      ))
                    )}
                  </section>
                </>
              ) : null}
            </div>
          ) : null}

          {activeTab === 'storage' ? (
            <div className="storage-panel">
              <div className="result-header">
                <h3>{tx(locale, 'Хранилище результатов', 'Result storage')}</h3>
                <button type="button" className="secondary compact" onClick={() => void loadLibrary()}>
                  {tx(locale, 'Обновить', 'Refresh')}
                </button>
              </div>
              <div className="library-grid">
                {jobLibrary.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`library-card ${selectedJobId === item.id ? 'active' : ''}`}
                    onClick={() => void handleOpenLibraryJob(item.id)}
                  >
                    <h3>{item.original_filename}</h3>
                    <p>{item.status} · {formatPercent(item.progress)}</p>
                    <p>{item.created_by_device}</p>
                    <p>{formatDate(item.created_at)}</p>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </main>
  )
}
