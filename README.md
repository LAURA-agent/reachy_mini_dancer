# Reachy Mini Dancer

**AI-powered choreography system for Reachy Mini robot**

A comprehensive choreography management system that combines manual control, pre-recorded moves, and AI-driven reactive choreography generation for Reachy Mini.

---

## Features

### 🎮 Web-Based Move Controller (`move_controller.html`)
- **Manual Position Control**: Precise 6-DOF head positioning with visual safety zones
- **Pre-Recorded Moves**: One-click access to 101+ moves (dances + emotions)
- **Choreography Builder**: Create and export custom routines
- **Live Monitoring**: Real-time 3D simulator view and pose visualization
- **Professional Branding**: Pollen Robotics color palette throughout

### 🤖 Reactive Choreographer (`choreography/`)
- **AI-Driven**: Uses LLM to analyze music and generate expressive choreography
- **Audio Analysis**: Essentia-based music feature extraction (tempo, energy, mood)
- **Context-Aware**: Adapts moves to musical segments and emotional context
- **Move Metadata**: Comprehensive tags for intelligent move selection

### 🎵 Choreography Player (`choreography_player.py`)
- Execute pre-made choreography JSON files
- Tempo-based timing with BPM support
- Amplitude and cycle control per move

---

## Quick Start

### Prerequisites

```bash
# Python 3.10+
# Reachy Mini SDK
# Reachy Mini daemon running on localhost:8100
```

### Installation

```bash
git clone https://github.com/LAURA-agent/reachy_mini_dancer.git
cd reachy_mini_dancer

# Install dependencies (create requirements.txt if needed)
pip install reachy-mini anthropic numpy scipy
```

### Configuration

**For AI choreography features**, create `choreography/config.py`:

```python
# Anthropic API configuration
ANTHROPIC_API_KEY = "your-api-key-here"
```

**Note:** This file is gitignored for security. Never commit API keys!

---

## Usage

### 1. Web-Based Move Controller

**Start the Reachy Mini daemon:**
```bash
mjpython -m reachy_mini.daemon.app.main --sim --scene minimal --fastapi-port 8100
```

**Open the controller:**
```bash
open move_controller.html
# Or drag into any web browser
```

**Features:**
- **Manual Control**: Adjust X, Y, Z position and Roll, Pitch, Yaw rotation
- **Execute Moves**: Select from 20 dances or 81 emotion moves
- **Build Choreographies**: Chain moves with BPM, amplitude, and cycle settings
- **Export**: Download JSON files ready to play

See `CHOREOGRAPHY_README.md` for detailed documentation.

---

### 2. Play Pre-Made Choreographies

```bash
python choreography_player.py examples/choreographies/dance_party.json
```

**Example choreographies included:**
- `simple_greeting.json` - 3-move welcoming sequence
- `dance_party.json` - 8-move high-energy performance
- `emotional_journey.json` - 9-move emotional storytelling
- `subtle_conversation.json` - 8-move conversational behaviors
- `energetic_performance.json` - 9-move maximum expressiveness

---

### 3. AI-Powered Reactive Choreography

**Generate choreography from music:**

```bash
cd choreography
python react_choreographer.py --audio path/to/song.mp3 --output generated_choreo.json
```

**How it works:**
1. **Audio Analysis**: Extracts tempo, energy, spectral features using Essentia
2. **Segmentation**: Divides music into meaningful segments
3. **LLM Generation**: Uses Anthropic Claude to select moves based on musical context
4. **Execution**: Plays generated choreography synchronized with music

**Configuration:**
- Edit `choreography/config.py` for API keys
- Adjust move metadata in `choreography/move_metadata.json`
- Customize prompt templates in `choreography/react_agent.py`

#### How the ReAct Agent Works

The reactive choreographer uses a **ReAct (Reasoning + Acting) pattern** with LLM-driven move selection:

**1. Audio Analysis Phase**
```
Audio File → Essentia Analysis → Musical Features
                                  ├── Tempo (BPM)
                                  ├── Energy levels
                                  ├── Spectral features
                                  └── Mood indicators
```

**2. Segmentation Phase**
```
Musical Features → Segment Analyzer → Timestamped Segments
                                      ├── Intro (0-8s, low energy)
                                      ├── Verse (8-24s, building)
                                      ├── Chorus (24-40s, high energy)
                                      └── etc.
```

**3. ReAct Loop (per segment)**
```
For each musical segment:
  ├── Context Builder: Assembles segment info + move metadata
  ├── LLM Reasoning: "This is a high-energy chorus, I should use..."
  ├── Tool Selection: Query move database by energy/emotion tags
  ├── Move Selection: LLM chooses from filtered candidates
  ├── Parameter Setting: Amplitude & cycles based on segment length
  └── Add to routine
```

**4. Output**
```
Choreography JSON → {bpm, routine: [{move, amplitude, cycles}, ...]}
```

**Key Components:**

- **`react_agent.py`**: Core ReAct loop with prompt templates and LLM calls
- **`audio_analyzer.py`**: Essentia-based music feature extraction
- **`segment_analyzer.py`**: Divides audio into meaningful sections
- **`move_metadata.py`**: Tags moves by energy, valence, emotion, motion type
- **`context_builder.py`**: Assembles rich context for LLM reasoning

#### Adapting to Other Inference Providers

**The system currently uses Anthropic's Claude**, but you can adapt it for other providers:

**For OpenAI GPT-4:**
```python
# In choreography/react_agent.py, replace:
from anthropic import Anthropic
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# With:
from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# Change message format from:
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    messages=[{"role": "user", "content": prompt}]
)

# To:
response = client.chat.completions.create(
    model="gpt-4-turbo",
    messages=[{"role": "user", "content": prompt}]
)
```

**For Local LLMs (Ollama, LM Studio):**
```python
import requests

def call_local_llm(prompt, model="llama3.1"):
    response = requests.post(
        "http://localhost:11434/api/generate",  # Ollama default
        json={
            "model": model,
            "prompt": prompt,
            "stream": False
        }
    )
    return response.json()["response"]
```

**For Hugging Face Inference:**
```python
from transformers import pipeline

generator = pipeline("text-generation", model="meta-llama/Llama-3.1-8B-Instruct")

def call_hf_model(prompt):
    return generator(prompt, max_new_tokens=500)[0]["generated_text"]
```

**Recommended Adaptations:**

1. **Keep the ReAct structure** - It works well for choreography reasoning
2. **Adjust prompt templates** - Different models need different prompt styles
3. **Test move selection quality** - Smaller models may need simpler metadata
4. **Consider caching** - Store generated choreographies to reduce API costs
5. **Add fallback logic** - If LLM fails, use rule-based move selection

**Performance Notes:**
- Claude Sonnet: Excellent reasoning, understands music context well
- GPT-4: Similar quality, slightly different move preferences
- Local LLMs (7B-13B): Usable but may need more explicit prompts
- Smaller models (<7B): Consider simplifying to rule-based with LLM assist

#### Desktop Viewer Audio Integration

The **desktop viewer** (`desktop_viewer.py`) includes an **"Add Audio"** button in the choreography window:

**Features:**
- **Recent Downloads**: Automatically scans `~/Downloads` for recent audio files
- **File Browser**: Click "Add Audio" to select any audio file (MP3, WAV, M4A)
- **Direct Generation**: Generates choreography from selected audio using ReAct agent
- **Instant Playback**: Loads generated routine and plays synchronized with music

**Usage:**
1. Launch viewer: `python desktop_viewer.py`
2. Navigate to "Choreography" tab
3. Click "Add Audio" button
4. Select audio file or pick from recent downloads
5. Wait for ReAct agent to generate choreography (~10-30 seconds)
6. Choreography auto-loads and plays with music

**Note:** Requires Anthropic API key configured in `choreography/config.py`

---

## File Structure

```
reachy_mini_dancer/
├── move_controller.html          # Web-based control interface
├── moves.json                     # Move library (101+ moves)
├── choreography_player.py         # Execute choreography JSON files
├── test_choreography.py           # Testing utilities
├── CHOREOGRAPHY_README.md         # Detailed web interface docs
├── choreography/                  # AI choreography package
│   ├── react_choreographer.py    # Main choreography generator
│   ├── react_agent.py            # LLM agent for move selection
│   ├── audio_analyzer.py         # Music analysis with Essentia
│   ├── segment_analyzer.py       # Musical segmentation
│   ├── move_metadata.py          # Move tagging and search
│   ├── move_metadata.json        # Comprehensive move metadata
│   └── README.md                 # Reactive choreography docs
└── examples/
    ├── choreographies/            # Example choreography JSON files
    └── debug/                     # Debugging utilities
```

---

## Move Library

### Dance Moves (20)
- `side_to_side_sway`, `jackson_square`, `dizzy_spin`, `stumble_and_recover`
- `pendulum_swing`, `head_tilt_roll`, `simple_nod`, `groovy_sway_and_roll`
- And more...

### Emotion Moves (81+)
- **Happy**: `cheerful1`, `enthusiastic1`, `laughing1`, `success1`
- **Sad**: `sad1`, `downcast1`, `lonely1`, `resigned1`
- **Surprised**: `surprised1`, `amazed1`, `oops1`
- **Curious**: `curious1`, `inquiring1`, `attentive1`
- **Angry**: `furious1`, `irritated1`, `rage1`
- And 60+ more emotions...

Full list in `moves.json` and `choreography/move_metadata.json`

---

## Choreography JSON Format

```json
{
  "bpm": 120,
  "routine": [
    {
      "move_name": "jackson_square",
      "amplitude": 1.0,
      "cycles": 4
    },
    {
      "move_name": "enthusiastic1",
      "amplitude": 1.2,
      "cycles": 2
    }
  ]
}
```

**Parameters:**
- `bpm`: Beats per minute for timing
- `move_name`: Name from move library
- `amplitude`: Movement intensity (0.5-2.0)
- `cycles`: Number of repetitions

---

## Development

### Testing

```bash
# Test choreography playback
python test_choreography.py

# Test move metadata
python choreography/move_metadata.py

# Test audio analysis
python choreography/audio_analyzer.py path/to/audio.mp3
```

### Adding New Moves

1. **Record move in SDK**
2. **Upload to Hugging Face dataset**
3. **Add to `moves.json`**:
   ```json
   {
     "dances": ["existing_moves", "your_new_move"],
     "emotions": [...]
   }
   ```
4. **Add metadata to `choreography/move_metadata.json`**:
   ```json
   {
     "your_new_move": {
       "energy": 0.7,
       "valence": 0.8,
       "emotional_tags": ["happy", "playful"],
       "motion_type": "rhythmic"
     }
   }
   ```

---

## Dependencies

### Core
- `reachy-mini` - Reachy Mini Python SDK
- `numpy` - Numerical computing
- `scipy` - Scientific computing

### AI Choreography
- `anthropic` - Claude API client
- `essentia` - Audio analysis
- `librosa` - Music processing (optional)

### Web Interface
- Modern web browser with JavaScript enabled
- Chart.js (loaded via CDN)

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly with real hardware
5. Submit a pull request

---

## License

Apache 2.0 (aligns with Reachy Mini SDK)

---

## Credits

**Created by:** Carson (LAURA Project Beta Tester)
**For:** Reachy Mini Beta Testing Program
**Organization:** Pollen Robotics

**Special Thanks:**
- Pollen Robotics for Reachy Mini hardware and SDK
- Anthropic for Claude AI API
- Essentia for audio analysis tools

---

## Safety Notes

⚠️ **Always test choreographies in simulation first**
⚠️ **Respect mechanical limits (±15° pitch, safe workspaces)**
⚠️ **Monitor robot during execution**
⚠️ **Keep emergency stop accessible**

---

## Support

- **Issues**: https://github.com/LAURA-agent/reachy_mini_dancer/issues
- **Reachy Mini Docs**: https://docs.pollen-robotics.com/
- **SDK**: https://github.com/pollen-robotics/reachy_mini

---

*This project demonstrates the creative possibilities of combining AI, robotics, and music for expressive robotic performance art.*
