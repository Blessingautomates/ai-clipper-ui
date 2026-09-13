"use client";

import { useState } from "react";
import { Sparkles, Download, Scissors, Link as LinkIcon, Upload, Film, FileVideo } from "lucide-react";

export default function Dashboard() {
  const [inputMode, setInputMode] = useState("url");
  const [url, setUrl] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);

  const [clips, setClips] = useState([
    {
      id: 1,
      title: "How AI Agents Will Replace Standard APIs",
      score: 98,
      duration: "00:45",
      videoUrl: "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
      caption: "AI agents are no longer just calling static endpoints; they are autonomously orchestrating multi-step workflows."
    },
    {
      id: 2,
      title: "Building Micro-SaaS Applications in 2026",
      score: 92,
      duration: "00:58",
      videoUrl: "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4",
      caption: "The speed of iteration is your biggest moat when shipping automated web tools."
    }
  ]);

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0]);
    }
  };

  const handleGenerate = async (e) => {
    e.preventDefault();
    if (inputMode === "url" && !url) return;
    if (inputMode === "upload" && !selectedFile) return;

    setIsProcessing(true);

    setTimeout(() => {
      setIsProcessing(false);
      setUrl("");
      setSelectedFile(null);
    }, 3500);
  };

  return (
    <div className="min-h-screen bg-[#0A0D14] text-slate-100 p-4 sm:p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        
        {/* Top Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-6">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2">
              <Scissors className="w-6 h-6 text-blue-500" /> Video Clipper Workspace
            </h1>
            <p className="text-xs text-slate-400 mt-1">
              Provide a YouTube link or upload a video file from your device to auto-generate shorts.
            </p>
          </div>
          <div className="bg-[#0F131C] border border-slate-800 px-4 py-2 rounded-xl text-xs flex items-center gap-3">
            <span className="text-slate-400">Available Credits:</span>
            <span className="text-blue-400 font-bold">25 Clips</span>
          </div>
        </div>

        {/* Input Card with Mode Tabs */}
        <div className="bg-[#0F131C] border border-slate-800 p-4 sm:p-6 rounded-2xl space-y-5 shadow-xl">
          
          {/* Mode Selector Tabs */}
          <div className="flex border-b border-slate-800 pb-3 gap-4">
            <button
              type="button"
              onClick={() => setInputMode("url")}
              className={`flex items-center gap-2 text-xs font-semibold pb-1 transition ${
                inputMode === "url"
                  ? "text-blue-400 border-b-2 border-blue-500"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <LinkIcon className="w-3.5 h-3.5" /> YouTube Link
            </button>
            <button
              type="button"
              onClick={() => setInputMode("upload")}
              className={`flex items-center gap-2 text-xs font-semibold pb-1 transition ${
                inputMode === "upload"
                  ? "text-blue-400 border-b-2 border-blue-500"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Upload className="w-3.5 h-3.5" /> Upload File
            </button>
          </div>

          <form onSubmit={handleGenerate} className="space-y-4">
            {inputMode === "url" ? (
              <div className="space-y-2">
                <label className="text-xs font-semibold text-slate-300 block">YouTube Video URL</label>
                <div className="relative">
                  <LinkIcon className="w-4 h-4 text-slate-500 absolute left-3 top-3.5" />
                  <input
                    type="url"
                    required
                    placeholder="https://www.youtube.com/watch?v=..."
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    className="w-full bg-[#161B26] border border-slate-700 rounded-xl pl-9 pr-4 py-2.5 text-sm text-white focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <label className="text-xs font-semibold text-slate-300 block">Select Video File (MP4, MOV, WEBM)</label>
                <div className="border-2 border-dashed border-slate-700 hover:border-slate-500 rounded-xl p-6 text-center bg-[#161B26] transition relative cursor-pointer">
                  <input
                    type="file"
                    accept="video/*"
                    onChange={handleFileChange}
                    className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
                  />
                  <div className="flex flex-col items-center justify-center space-y-2">
                    <FileVideo className="w-8 h-8 text-slate-400" />
                    {selectedFile ? (
                      <p className="text-xs text-blue-400 font-semibold">{selectedFile.name}</p>
                    ) : (
                      <>
                        <p className="text-xs text-slate-300 font-medium">Click or drag & drop a video file here</p>
                        <p className="text-[10px] text-slate-500">Max size 500MB</p>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )}

            <button
              type="submit"
              disabled={isProcessing}
              className="w-full sm:w-auto bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-semibold px-6 py-2.5 rounded-xl text-sm flex items-center justify-center gap-2 transition"
            >
              {isProcessing ? (
                <>
                  <Film className="w-4 h-4 animate-spin" /> Extracting & Clipping...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" /> Generate Short Clips
                </>
              )}
            </button>
          </form>
        </div>

        {/* Generated Clips Output Grid */}
        <div className="space-y-4">
          <h2 className="text-lg font-bold text-white">Generated Short Clips</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {clips.map((clip) => (
              <div key={clip.id} className="bg-[#0F131C] border border-slate-800 rounded-2xl overflow-hidden flex flex-col justify-between">
                <div>
                  <div className="relative aspect-[9/16] bg-black max-h-72 w-full flex items-center justify-center overflow-hidden">
                    <video src={clip.videoUrl} controls className="h-full w-full object-cover" />
                    <span className="absolute top-3 left-3 bg-blue-600/90 text-white text-[10px] font-extrabold px-2 py-0.5 rounded-full">
                      Viral Score: {clip.score}
                    </span>
                    <span className="absolute bottom-3 right-3 bg-black/80 text-slate-300 text-[10px] px-2 py-0.5 rounded">
                      {clip.duration}
                    </span>
                  </div>

                  <div className="p-4 space-y-2">
                    <h3 className="font-semibold text-sm text-white line-clamp-1">{clip.title}</h3>
                    <p className="text-xs text-slate-400 line-clamp-2">{clip.caption}</p>
                  </div>
                </div>

                <div className="p-4 pt-0">
                  <a
                    href={clip.videoUrl}
                    download
                    target="_blank"
                    rel="noreferrer"
                    className="w-full bg-[#161B26] hover:bg-[#1E2433] border border-slate-700 text-slate-200 text-xs font-semibold py-2 rounded-xl flex items-center justify-center gap-2 transition"
                  >
                    <Download className="w-3.5 h-3.5" /> Download HD Clip
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
