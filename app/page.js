"use client";

import { useState } from "react";
import { Sparkles, Youtube, Video, Download, RefreshCw, CheckCircle, AlertCircle, Layers } from "lucide-react";

export default function Home() {
  const [videoUrl, setVideoUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!videoUrl) return;

    setLoading(true);
    setStatus({ type: "info", text: "Connecting to processing engine..." });
    setResult(null);

    try {
      const response = await fetch("/api/webhook", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ videoUrl }),
      });

      const resData = await response.json();

      if (response.ok && resData.success) {
        setStatus({ type: "success", text: "Clips generated successfully!" });
        setResult(resData.data);
      } else {
        setStatus({ type: "error", text: resData.error || "Failed to process video." });
      }
    } catch (err) {
      setStatus({ type: "error", text: "Network error: " + err.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0A0D14] text-slate-100 flex flex-col font-sans">
      <header className="border-b border-slate-800 bg-[#0F131C] sticky top-0 z-50 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="bg-blue-600 p-2 rounded-xl text-white">
            <Sparkles className="w-5 h-5" />
          </div>
          <span className="font-bold text-lg text-white">
            ToolStack Clipper
          </span>
        </div>
        <nav className="flex items-center gap-4 text-sm font-medium text-slate-400">
          <a href="#" className="text-white hover:text-blue-400">Dashboard</a>
          <a href="#" className="hover:text-blue-400">Clips</a>
          <a href="#" className="hover:text-blue-400">Affiliate</a>
        </nav>
      </header>

      <main className="flex-1 max-w-4xl w-full mx-auto p-4 sm:p-6 space-y-8">
        <div className="text-center space-y-3 pt-6">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 text-xs font-semibold uppercase">
            <Sparkles className="w-3.5 h-3.5" /> AI Short-Form Engine
          </div>
          <h1 className="text-3xl sm:text-4xl font-extrabold text-white">
            Turn Long YouTube Videos into Viral Shorts
          </h1>
          <p className="text-slate-400 text-sm sm:text-base max-w-xl mx-auto">
            Paste your video URL below to extract, auto-caption, and format viral clips instantly.
          </p>
        </div>

        <div className="bg-[#0F131C] border border-slate-800 rounded-2xl p-4 sm:p-6 shadow-2xl space-y-4">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Youtube className="w-4 h-4 text-red-500" /> YouTube Video Link
              </label>
              <input
                type="url"
                placeholder="https://youtu.be/..."
                value={videoUrl}
                onChange={(e) => setVideoUrl(e.target.value)}
                className="w-full bg-[#161B26] border border-slate-700 rounded-xl px-4 py-3.5 text-sm text-white focus:outline-none focus:border-blue-500"
                required
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-semibold py-3.5 px-4 rounded-xl flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" /> Processing Video...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" /> Generate Viral Clips
                </>
              )}
            </button>
          </form>

          {status && (
            <div
              className={`p-3.5 rounded-xl border text-sm flex items-start gap-3 ${
                status.type === "success"
                  ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                  : status.type === "error"
                  ? "bg-red-500/10 border-red-500/20 text-red-400"
                  : "bg-blue-500/10 border-blue-500/20 text-blue-400"
              }`}
            >
              {status.type === "success" && <CheckCircle className="w-5 h-5 shrink-0" />}
              {status.type === "error" && <AlertCircle className="w-5 h-5 shrink-0" />}
              {status.type === "info" && <RefreshCw className="w-5 h-5 animate-spin shrink-0" />}
              <div>{status.text}</div>
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <h2 className="font-bold text-lg text-white flex items-center gap-2">
              <Video className="w-5 h-5 text-blue-400" /> Generated Clips
            </h2>
            <span className="text-xs text-slate-500 font-medium">Output: 9:16 Vertical Format</span>
          </div>

          {result ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="bg-[#0F131C] border border-slate-800 rounded-2xl p-4 space-y-3">
                <div className="h-64 bg-[#161B26] rounded-xl flex items-center justify-center border border-slate-800 relative">
                  <Video className="w-10 h-10 text-slate-600" />
                  <span className="absolute bottom-3 left-3 bg-black/60 px-2 py-1 rounded text-[10px] text-slate-300 font-mono">
                    Viral Score: 98%
                  </span>
                </div>
                <div className="space-y-2">
                  <h3 className="font-semibold text-sm text-slate-200">Extracted High Hook Moment</h3>
                  <button className="w-full bg-slate-800 hover:bg-slate-700 text-xs text-slate-200 py-2 rounded-lg font-medium flex items-center justify-center gap-1.5">
                    <Download className="w-3.5 h-3.5" /> Download Clip
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-[#0F131C] border border-dashed border-slate-800 rounded-2xl p-8 text-center space-y-2">
              <Layers className="w-8 h-8 text-slate-600 mx-auto" />
              <p className="text-sm text-slate-400 font-medium">No clips generated yet</p>
              <p className="text-xs text-slate-500 max-w-xs mx-auto">
                Paste a YouTube video link above and click generate to populate your vertical clips here.
              </p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
