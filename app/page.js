import Link from "next/link";
import { Sparkles, Video, ArrowRight } from "lucide-react";

export default function HomePage() {
  return (
    <main className="min-h-screen bg-[#0A0D14] text-white flex flex-col items-center justify-center p-6 text-center space-y-6">
      <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 text-xs font-semibold">
        <Sparkles className="w-3.5 h-3.5" /> ToolStack AI
      </div>

      <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl max-w-xl">
        Turn Long YouTube Videos Into Viral Shorts
      </h1>

      <p className="text-slate-400 max-w-md text-sm">
        Extract, auto-caption, and format high-converting short clips in seconds.
      </p>

      <div className="flex items-center gap-4">
        <Link
          href="/login"
          className="bg-blue-600 hover:bg-blue-500 text-white font-medium px-6 py-3 rounded-xl text-sm flex items-center gap-2 transition"
        >
          Get Started <ArrowRight className="w-4 h-4" />
        </Link>
      </div>
    </main>
  );
}
