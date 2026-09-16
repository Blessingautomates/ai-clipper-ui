'use client';

import { useState } from 'react';

export default function DashboardPage() {
  const [videoUrl, setVideoUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('');
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    e.stopPropagation();

    if (!videoUrl) return;

    setLoading(true);
    setError(null);
    setResult(null);
    setStatusText('Extracting and processing video via Python backend...');

    try {
      // Points directly to your Python backend endpoint URL
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_BACKEND_URL || '/api/process-video';

      const response = await fetch(backendUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: videoUrl }),
      });

      if (!response.ok) {
        throw new Error(`Python API returned status: ${response.status}`);
      }

      const data = await response.json();
      setResult(data);
      setStatusText('Processing Complete!');
    } catch (err: any) {
      console.error('Processing error:', err);
      setError(err.message || 'Failed to process video with Python');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0B0F19] text-white p-6">
      <div className="max-w-3xl mx-auto space-y-6">
        <h1 className="text-3xl font-bold">Python AI Video Clipper</h1>
        
        <form onSubmit={handleGenerate} className="space-y-4 bg-[#111827] p-6 rounded-xl border border-gray-800">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Video URL
            </label>
            <input
              type="url"
              required
              value={videoUrl}
              onChange={(e) => setVideoUrl(e.target.value)}
              placeholder="https://youtube.com/watch?v=..."
              className="w-full px-4 py-2 bg-[#1F2937] border border-gray-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 rounded-lg font-medium transition-colors disabled:opacity-50"
          >
            {loading ? statusText : 'Generate Clips'}
          </button>
        </form>

        {error && (
          <div className="p-4 bg-red-950/50 border border-red-800 text-red-400 rounded-xl text-sm">
            {error}
          </div>
        )}

        {result && (
          <div className="p-6 bg-[#111827] border border-gray-800 rounded-xl space-y-3">
            <h2 className="text-xl font-semibold text-green-400">Clips Ready!</h2>
            <pre className="bg-[#1F2937] p-4 rounded-lg overflow-x-auto text-xs text-gray-300">
              {JSON.stringify(result, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
