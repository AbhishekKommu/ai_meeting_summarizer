# AI Meeting Summarizer

A Flask web app that transcribes meeting audio and generates concise AI summaries.
Includes authentication (register / login / logout) with session-based access control.

## Features

- 🔐 User registration & login (hashed passwords with Werkzeug)
- 🎙️ Upload audio/video files (mp3, wav, m4a, mp4, webm, ogg)
- 🧠 AI summary generation (mock implementation — plug in Whisper + GPT)
- 📜 Persistent history per user (SQLite)
- 🎨 Beautiful glassmorphism UI with animated gradient background

## Project Structure

```
ai_meeting_summarizer/
├── app.py
├── requirements.txt
├── README.md
├── database/
│   └── meetings.db          # auto-created on first run
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   └── index.html
└── static/
    ├── style.css
    └── script.js
```

## Setup

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate          # Linux / macOS
venv\Scripts\activate             # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

Then open **http://localhost:5000** in your browser.

## Usage

1. Click **Register** to create an account.
2. Log in with your credentials.
3. Upload a meeting recording and click **Summarize**.
4. View your summary instantly and browse past meetings in the history panel.

## Plugging in a Real AI Pipeline

Replace `summarize_audio()` in `app.py` with:

```python
import whisper
from transformers import pipeline

def summarize_audio(filepath, original_name):
    model = whisper.load_model("base")
    result = model.transcribe(filepath)
    transcript = result["text"]

    summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
    chunks = [transcript[i:i+1000] for i in range(0, len(transcript), 1000)]
    summaries = [summarizer(c, max_length=150, min_length=40, do_sample=False)[0]["summary_text"]
                 for c in chunks]
    return "\n".join(summaries)
```

## Security Notes

- Change `app.secret_key` in `app.py` before deploying.
- Use environment variables for secrets in production.
- Consider rate-limiting `/summarize` for public deployments.

## License

MIT — free to use and modify.