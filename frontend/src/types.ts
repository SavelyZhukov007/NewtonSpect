export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export type ExportFormat = 'srt' | 'vtt' | 'ass' | 'mp4_burned' | 'zip'

export interface JobOptions {
  language: string | null
  auto_detect_language: boolean
  quality_preset: 'max_quality' | 'balanced' | 'max_speed'
  whisper_model: string
  export_formats: ExportFormat[]
  detect_people: boolean
  generate_summary: boolean
  enable_active_speaker_model: boolean
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
  status: JobStatus
  progress: number
  current_step: string | null
  error_message: string | null
  options: JobOptions
  artifacts: Artifact[]
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

