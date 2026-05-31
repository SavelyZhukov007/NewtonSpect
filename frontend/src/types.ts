export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export type ExportFormat = string

export interface JobOptions {
  language: string | null
  auto_detect_language: boolean
  quality_preset: 'max_quality' | 'balanced' | 'max_speed'
  whisper_model: string
  export_formats: ExportFormat[]
  detect_people: boolean
  generate_summary: boolean
  enable_active_speaker_model: boolean
  enable_mask_overlay: boolean
  mask_model_names: string[]
  enable_subtitles: boolean
  enable_burned_video: boolean
  ui_locale: 'ru' | 'en'
  streaming_mode: 'dual_pass_hq' | 'final_only_hq' | 'live_only_fast'
  camera_mode: boolean
  auto_stop_seconds: number
  show_face_mask_preview: boolean
  output_video_format: string
  subtitle_embed_mode: 'auto' | 'embedded' | 'sidecar' | 'burned'
  subtitle_style: Record<string, unknown>
}

export interface StageRuntime {
  step: string
  progress: number
  speed: number | null
  speed_unit: string | null
  eta_seconds: number | null
  message: string | null
  started_at: string | null
  updated_at: string | null
  completed: boolean
}

export interface JobRuntime {
  stages: Record<string, StageRuntime>
  overall_eta_seconds: number | null
  current_speed: number | null
  current_speed_unit: string | null
}

export interface Artifact {
  name: string
  kind: string
  path: string
  mime_type: string
  created_at: string
}

export interface JobView {
  id: string
  original_filename: string
  created_by_device: string
  locale: 'ru' | 'en'
  status: JobStatus
  progress: number
  current_step: string | null
  error_message: string | null
  options: JobOptions
  artifacts: Artifact[]
  runtime: JobRuntime
  created_at: string
  updated_at: string
}

export interface PersonTrackStats {
  screen_time_seconds: number
  first_seen: number
  last_seen: number
  avg_confidence: number
  speaking_seconds: number
}

export interface PersonProfile {
  person_id: string
  display_name: string | null
  display_name_confidence: number
  portrait_path: string | null
  track_stats: PersonTrackStats
  key_comments: string[]
}

export interface VideoReport {
  summary_md: string
  latex_blocks: string[]
  key_topics: string[]
  people_highlights: Record<string, string[]>
  raw_markdown: string
}

export interface JobLibraryResponse {
  items: JobView[]
}

export interface VideoFormatCapability {
  format: string
  ffmpeg_muxer: string
  curated: boolean
  can_embed_subtitles: boolean
  preferred_subtitle_codec: string | null
  notes: string | null
}

export interface FormatCapabilitiesResponse {
  curated: VideoFormatCapability[]
  all_muxers: VideoFormatCapability[]
}
