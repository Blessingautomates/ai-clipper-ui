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
      const response = await fetch("/api/webhook", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ videoUrl: url }),
      });

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.error || `Server responded with status ${response.status}`);
      }

      setMessage("Workflow triggered successfully!");
    } catch (err) {
      setMessage(`Error: ${err.message}`);
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
      {message && (
        <p style={{ marginTop: "15px", fontSize: "0.85rem", color: message.startsWith("Error:") ? "#f87171" : "#4ade80" }}>
          {message}
        </p>
      )}
    </main>
  );
}
