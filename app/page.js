"use client";
import { useState } from "react";

export default function Home() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage("");

    try {
      // REPLACE THE URL BELOW WITH YOUR N8N PRODUCTION WEBHOOK URL
      const response = await fetch("https://nextgenzauto.app.n8n.cloud/webhook-test/generate-clips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ videoUrl: url }),
      });

      if (!response.ok) throw new Error("Failed to connect to n8n workflow");

      const data = await response.json();
      setMessage("Workflow triggered successfully!");
    } catch (err) {
      setMessage("Could not connect to n8n webhook. Make sure your workflow is active.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#fff", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "20px" }}>
      <h1 style={{ fontSize: "1.2rem", color: "#38bdf8", marginBottom: "20px" }}>ToolStack Clipper</h1>
      <form onSubmit={handleSubmit} style={{ width: "100%", maxWidth: "400px", display: "flex", flexDirection: "column", gap: "10px" }}>
        <input
          type="text"
          placeholder="Paste YouTube URL..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          style={{ padding: "12px", borderRadius: "6px", border: "1px solid #334155", backgroundColor: "#1e293b", color: "#fff" }}
        />
        <button
          type="submit"
          disabled={loading}
          style={{ padding: "12px", borderRadius: "6px", backgroundColor: "#0284c7", color: "#fff", fontWeight: "bold", border: "none", cursor: "pointer" }}
        >
          {loading ? "Processing..." : "Generate Viral Clips"}
        </button>
      </form>
      {message && <p style={{ marginTop: "15px", fontSize: "0.85rem", color: "#f87171" }}>{message}</p>}
    </main>
  );
}
