const express = require('express');
const cors = require('cors');
const { exec } = require('child_process');

const app = express();
app.use(cors());
app.use(express.json());

// 1. Video processing bridge endpoint
app.post('/api/clipper/process', async (req, res) => {
  const { videoUrl } = req.body;
  if (!videoUrl) return res.status(400).json({ error: 'Video URL is required' });

  console.log(`[Clipper Bridge] Executing yt-dlp for: ${videoUrl}`);
  const cmd = `python3 -m yt_dlp -g "${videoUrl}"`;

  exec(cmd, (error, stdout, stderr) => {
    if (error) return res.status(500).json({ error: 'Processing failed', details: error.message });
    return res.json({
      success: true,
      message: 'Processing complete',
      clips: [{ id: Date.now(), score: 95, headline: 'Extracted Clip via Termux', duration: '0:59' }]
    });
  });
});

// 2. AI Virality Analysis route using deepseek-v4-flash via AgentRouter
app.post('/api/clipper/analyze', async (req, res) => {
  const { transcript } = req.body;
  if (!transcript) return res.status(400).json({ error: 'Transcript is required' });

  try {
    const response = await fetch('https://co.agentrouter.org/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer sk-mbvvigzdHF63xDDboOL35ZxygDOyp3M4Pbamf9rVzxZuvbui',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'
      },
      body: JSON.stringify({
        model: 'deepseek-v4-flash',
        messages: [
          { 
            role: 'system', 
            content: 'You are an expert video editor. Analyze the transcript with timestamps and select top viral clips as a JSON array with keys: title, start_time, end_time, virality_score.' 
          },
          { role: 'user', content: transcript }
        ]
      })
    });

    const data = await response.json();
    return res.json({ success: true, analysis: data });
  } catch (error) {
    console.error('[AI Error]:', error.message);
    return res.status(500).json({ error: 'AI Analysis failed', details: error.message });
  }
});

const PORT = 5001;
app.listen(PORT, () => console.log(`🚀 Clipper Bridge running on http://localhost:${PORT}`));
