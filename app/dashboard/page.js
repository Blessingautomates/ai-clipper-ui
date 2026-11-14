"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Download, Scissors, Sparkles } from 'lucide-react';

async function readResponse(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in to continue.' : data.error || 'Request failed');
  return data;
}

function ClipCard({ jobId, clip }) {
  const [playback, setPlayback] = useState('');
  const [error, setError] = useState('');
  const [opening, setOpening] = useState(false);
  const endpoint = '/api/jobs/' + jobId + '/clips/' + clip.clip_number;

  const openClip = async (download = false) => {
    setOpening(true);
    setError('');
    try {
      const data = await readResponse(await fetch(endpoint + (download ? '?download=1' : ''), { cache: 'no-store' }));
      if (download) {
        const anchor = document.createElement('a');
        anchor.href = data.url;
        anchor.download = 'clip-' + clip.clip_number + '.mp4';
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } else {
        setPlayback(data.url);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setOpening(false);
    }
  };

  return (
    <article className="rounded-2xl border border-slate-800 bg-[#0F131C] p-4 space-y-3">
      <h3 className="font-semibold">{clip.title}</h3>
      {playback ? (
        <video src={playback} controls playsInline preload="metadata"
          className="aspect-video w-full rounded-lg bg-black" onError={() => {
            setPlayback('');
            setError('Playback link expired or unavailable. Click Play to get a fresh link.');
          }} />
      ) : <button type="button" onClick={() => openClip()} disabled={opening}
        className="aspect-video w-full rounded-lg bg-black text-blue-400">{opening ? 'Opening…' : 'Play clip'}</button>}
      <p className="text-xs text-slate-400">{Math.round(clip.duration_seconds)} seconds · MP4 · {clip.width} × {clip.height}</p>
      {clip.selection_source === 'preview' ? <p className="text-xs text-slate-400">Preview selection</p>
        : clip.rationale && <p className="text-sm text-slate-400">{clip.rationale}</p>}
      {clip.suggested_caption && <p className="text-sm text-slate-400">{clip.suggested_caption}</p>}
      <button type="button" onClick={() => openClip(true)} disabled={opening}
        className="flex items-center gap-2 rounded-lg border border-slate-700 px-4 py-2 text-sm disabled:opacity-50">
        <Download size={16} /> Download MP4
      </button>
      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    </article>
  );
}

export default function Dashboard() {
  const [videoUrl, setVideoUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const mounted = useRef(false);

  const loadJobs = useCallback(async () => {
    try {
      const data = await readResponse(await fetch('/api/jobs', { cache: 'no-store' }));
      if (mounted.current) {
        setJobs(data.jobs || []);
        setError('');
      }
    } catch (err) {
      if (mounted.current) setError(err.message);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let timer;
    async function poll() {
      await loadJobs();
      if (mounted.current) timer = setTimeout(poll, 5000);
    }
    poll();
    return () => { mounted.current = false; clearTimeout(timer); };
  }, [loadJobs]);

  async function handleGenerate(event) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    setNotice('');
    try {
      const data = await readResponse(await fetch('/api/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ videoUrl: videoUrl.trim() }),
      }));
      setJobs((previous) => [data.job, ...previous.filter((job) => job.id !== data.job.id)]);
      setVideoUrl('');
      setNotice(data.workerDispatch === 'dispatched' ? 'Job queued. Processing will start when a worker is available.'
        : 'Job saved. Waiting for the processing worker to pick it up.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#0A0D14] text-slate-100 p-4 sm:p-8">
      <div className="mx-auto max-w-6xl space-y-8">
        <div className="flex items-center justify-between border-b border-slate-800 pb-6">
          <h1 className="text-2xl font-bold flex items-center gap-2"><Scissors className="text-blue-500" /> Video Clipper</h1>
          <Link href="/login" className="text-sm text-blue-400">Account</Link>
        </div>
        <form onSubmit={handleGenerate} className="rounded-2xl border border-slate-800 bg-[#0F131C] p-6 space-y-4">
          <label htmlFor="video-url" className="block font-semibold">Public video URL</label>
          <p className="text-sm text-slate-400">Paste a direct video file link you have permission to process. Up to 256 MB and 30 minutes. We’ll render up to three MP4 clips.</p>
          <input id="video-url" type="url" required maxLength={4096} value={videoUrl}
            onChange={(event) => setVideoUrl(event.target.value)} placeholder="https://example.com/video.mp4"
            className="w-full rounded-xl border border-slate-700 bg-[#161B26] px-4 py-3" />
          <button type="submit" disabled={submitting} className="flex items-center gap-2 rounded-xl bg-blue-600 px-6 py-3 disabled:opacity-50">
            <Sparkles size={16} /> {submitting ? 'Queueing…' : 'Generate clips'}
          </button>
        </form>
        {error && <p role="alert" className="text-red-400">{error}</p>}
        {notice && <p role="status" className="text-blue-400">{notice}</p>}
        <section className="space-y-6" aria-label="Your video jobs">
          <h2 className="text-lg font-bold">Your videos</h2>
          {loading && <p className="text-slate-400">Loading jobs…</p>}
          {!loading && !jobs.length && <p className="text-slate-400">Submit a video to create your first clips.</p>}
          {jobs.map((job) => (
            <section key={job.id} className="space-y-3 rounded-2xl border border-slate-800 p-4">
              <p className="truncate text-sm text-slate-300">{job.video_url}</p>
              <p role="status" className="text-sm text-blue-400">{({ pending: 'Queued', processing: 'Processing video…', completed: 'Clips ready', failed: 'Processing failed' })[job.status] || job.status}</p>
              {job.status === 'failed' && <p className="text-sm text-red-400">{job.error_message || 'Unable to process this video. Check the URL and try again.'}</p>}
              {job.status === 'completed' && <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {(Array.isArray(job.clips) ? job.clips : []).filter((clip) => clip.object_path).map((clip) =>
                  <ClipCard key={clip.clip_number} jobId={job.id} clip={clip} />)}
              </div>}
            </section>
          ))}
        </section>
      </div>
    </main>
  );
}
