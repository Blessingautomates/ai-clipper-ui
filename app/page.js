'use client';

import { useState } from 'react';

export default function Home() {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [clips, setClips] = useState([]);
  const [error, setError] = useState('');

  const handleGenerate = async () => {
    if (!url) return;

    setLoading(true);
    setError('');
    setClips([]);

    try {
      const res = await fetch('https://nextgenzauto.app.n8n.cloud/webhook/clipper-trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ videoUrl: url }),
      });

      if (!res.ok) throw new Error('Failed to generate clips from workflow');

      const data = await res.json();
      setClips(data.clips || []);
    } catch (err) {
      console.error(err);
      setError('Could not connect to n8n webhook. Make sure Cloudflare Tunnel and server.js are active.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#f8fafc', padding: '2rem 1rem', fontFamily: 'sans-serif' }}>
      <div style={{ maxWidth: '600px', margin: '0 auto' }}>
        <h1 style={{ textAlign: 'center', color: '#38bdf8' }}>ToolStack Clipper</h1>
        
        <div style={{ backgroundColor: '#1e293b', padding: '1.5rem', borderRadius: '12px', marginTop: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <input
            type="url"
            placeholder="Paste YouTube video link..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            style={{ padding: '0.8rem', borderRadius: '8px', border: '1px solid #334155', backgroundColor: '#0f172a', color: '#fff' }}
          />
          <button
            type="button"
            onClick={handleGenerate}
            disabled={loading}
            style={{ padding: '0.9rem', backgroundColor: loading ? '#64748b' : '#0284c7', color: '#fff', border: 'none', borderRadius: '8px', fontWeight: 'bold', cursor: 'pointer' }}
          >
            {loading ? 'Analyzing & Clipping Video...' : 'Generate Viral Clips'}
          </button>
        </div>

        {error && <p style={{ color: '#fca5a5', marginTop: '1rem' }}>{error}</p>}

        {clips.length > 0 && (
          <div style={{ marginTop: '2rem' }}>
            <h2>Extracted Clips</h2>
            {clips.map((clip, i) => (
              <div key={i} style={{ backgroundColor: '#1e293b', padding: '1rem', borderRadius: '8px', marginBottom: '1rem' }}>
                <h3 style={{ color: '#38bdf8', margin: 0 }}>{clip.headline || 'Clip'}</h3>
                <p style={{ margin: 0, color: '#94a3b8' }}>Score: {clip.score}/100</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
