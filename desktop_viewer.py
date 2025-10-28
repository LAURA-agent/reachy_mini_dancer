import glfw
import mujoco
import numpy as np
from OpenGL.GL import *
import os
import time
import argparse
import imgui
import json
import requests
import threading
from websocket import WebSocketApp
from imgui.integrations.opengl import FixedPipelineRenderer
from imgui.integrations import compute_fb_scale
from datetime import datetime
import subprocess
import sys
import pygame

# Import the backend
from reachy_mini.daemon.backend.mujoco import MujocoBackend

# Import choreography modules
from choreography.react_agent import ReActChoreographer
from choreography_player import Choreography
from reachy_mini import ReachyMini
from reachy_mini.motion.recorded_move import RecordedMoves

# Custom GLFW renderer using Fixed Pipeline (OpenGL 2.1 compatible)
class GlfwFixedRenderer(FixedPipelineRenderer):
    """ImGui GLFW renderer using OpenGL 2.1 fixed pipeline (compatible with MuJoCo)."""

    def __init__(self, window, attach_callbacks=True):
        super(GlfwFixedRenderer, self).__init__()
        self.window = window

        if attach_callbacks:
            glfw.set_key_callback(self.window, self.keyboard_callback)
            glfw.set_cursor_pos_callback(self.window, self.mouse_callback)
            glfw.set_window_size_callback(self.window, self.resize_callback)
            glfw.set_char_callback(self.window, self.char_callback)
            glfw.set_scroll_callback(self.window, self.scroll_callback)

        self.io.display_size = glfw.get_framebuffer_size(self.window)
        self.io.get_clipboard_text_fn = self._get_clipboard_text
        self.io.set_clipboard_text_fn = self._set_clipboard_text

        self._map_keys()
        self._gui_time = None

    def _get_clipboard_text(self):
        return glfw.get_clipboard_string(self.window)

    def _set_clipboard_text(self, text):
        glfw.set_clipboard_string(self.window, text)

    def _map_keys(self):
        key_map = self.io.key_map
        key_map[imgui.KEY_TAB] = glfw.KEY_TAB
        key_map[imgui.KEY_LEFT_ARROW] = glfw.KEY_LEFT
        key_map[imgui.KEY_RIGHT_ARROW] = glfw.KEY_RIGHT
        key_map[imgui.KEY_UP_ARROW] = glfw.KEY_UP
        key_map[imgui.KEY_DOWN_ARROW] = glfw.KEY_DOWN
        key_map[imgui.KEY_PAGE_UP] = glfw.KEY_PAGE_UP
        key_map[imgui.KEY_PAGE_DOWN] = glfw.KEY_PAGE_DOWN
        key_map[imgui.KEY_HOME] = glfw.KEY_HOME
        key_map[imgui.KEY_END] = glfw.KEY_END
        key_map[imgui.KEY_DELETE] = glfw.KEY_DELETE
        key_map[imgui.KEY_BACKSPACE] = glfw.KEY_BACKSPACE
        key_map[imgui.KEY_ENTER] = glfw.KEY_ENTER
        key_map[imgui.KEY_ESCAPE] = glfw.KEY_ESCAPE
        key_map[imgui.KEY_A] = glfw.KEY_A
        key_map[imgui.KEY_C] = glfw.KEY_C
        key_map[imgui.KEY_V] = glfw.KEY_V
        key_map[imgui.KEY_X] = glfw.KEY_X

    def keyboard_callback(self, window, key, scancode, action, mods):
        io = self.io
        if action == glfw.PRESS:
            io.keys_down[key] = True
        elif action == glfw.RELEASE:
            io.keys_down[key] = False

        io.key_ctrl = io.keys_down[glfw.KEY_LEFT_CONTROL] or io.keys_down[glfw.KEY_RIGHT_CONTROL]
        io.key_alt = io.keys_down[glfw.KEY_LEFT_ALT] or io.keys_down[glfw.KEY_RIGHT_ALT]
        io.key_shift = io.keys_down[glfw.KEY_LEFT_SHIFT] or io.keys_down[glfw.KEY_RIGHT_SHIFT]

    def char_callback(self, window, char):
        if 0 < char < 0x10000:
            self.io.add_input_character(char)

    def resize_callback(self, window, width, height):
        self.io.display_size = width, height

    def mouse_callback(self, *args, **kwargs):
        pass

    def scroll_callback(self, window, x_offset, y_offset):
        self.io.mouse_wheel = y_offset

    def process_inputs(self):
        io = imgui.get_io()
        window_size = glfw.get_window_size(self.window)
        fb_size = glfw.get_framebuffer_size(self.window)

        io.display_size = window_size
        io.display_fb_scale = compute_fb_scale(window_size, fb_size)

        if glfw.get_window_attrib(self.window, glfw.FOCUSED):
            io.mouse_pos = glfw.get_cursor_pos(self.window)
        else:
            io.mouse_pos = -1, -1

        io.mouse_down[0] = glfw.get_mouse_button(self.window, 0)
        io.mouse_down[1] = glfw.get_mouse_button(self.window, 1)
        io.mouse_down[2] = glfw.get_mouse_button(self.window, 2)

        current_time = glfw.get_time()
        if self._gui_time:
            self.io.delta_time = current_time - self._gui_time
        else:
            self.io.delta_time = 1. / 60.
        if io.delta_time <= 0.0:
            io.delta_time = 1. / 1000.
        self._gui_time = current_time

# --- Global variables ---
backend = None
model = None
data = None
reachy = None
dances_library = None
emotions_library = None
choreography_context = None # Single source of truth
sdk_initialized = False
current_choreo_move_info = ""

cam = mujoco.MjvCamera()
opt = mujoco.MjvOption()
scn = mujoco.MjvScene()
ctx = mujoco.MjrContext()

button_left = False
button_middle = False
button_right = False
lastx = 0
lasty = 0

# Fullscreen state
is_fullscreen = False
windowed_width = 1200
windowed_height = 900
windowed_xpos = 100
windowed_ypos = 100

# Daemon connection
daemon_url = "http://localhost:8100"
daemon_connected = False
last_move_uuid = None

# Move library
dances = []
emotions = []
selected_dance = 0
selected_emotion = 0
selected_dataset = "dances"  # or "emotions"

# UI state
panel_collapsed = False
status_message = ""
status_message_time = 0

# WebSocket state
latest_daemon_state = None
state_lock = threading.Lock()
ws_thread = None
ws_app = None

# Manual control state (all angles in DEGREES for UI)
manual_x = 0.0
manual_y = 0.0
manual_z = 0.0  # Neutral position, not elevated
manual_yaw = 0.0  # Head yaw in degrees
manual_pitch = 0.0  # Head pitch in degrees
manual_roll = 0.0  # Head roll in degrees
manual_body_yaw = 0.0  # Body yaw in degrees
manual_left_antenna = 0.0  # Antenna in radians (kept as radians)
manual_right_antenna = 0.0  # Antenna in radians (kept as radians)
manual_duration = 0.5  # Default movement duration in seconds

# Yaw binding state
bind_yaw = False  # Synchronize head and body yaw movement
bind_antennas = False # Synchronize antennas movement

# Yaw safety constants (in degrees)
MAX_YAW_DIFF_DEG = 65.0  # Maximum safe head-body yaw difference
HEAD_YAW_LIMIT = 180.0  # Head yaw range: ±180°
BODY_YAW_LIMIT = 160.0  # Body yaw range: ±160°

# Choreography builder state
class AudioAnalysisState:
    """Protected state container for audio analysis data."""
    def __init__(self):
        self._audio_path = None
        self._analysis_data = None

    @property
    def audio_path(self):
        return self._audio_path

    @audio_path.setter
    def audio_path(self, value):
        self._audio_path = value
        # Clear analysis when audio path changes
        self._analysis_data = None

    @property
    def analysis(self):
        """Read-only access to analysis data."""
        return self._analysis_data

    def set_analysis(self, data):
        """Only way to set analysis data - must be called from analyze_audio()."""
        self._analysis_data = data

    def clear(self):
        """Clear both audio path and analysis."""
        self._audio_path = None
        self._analysis_data = None

audio_state = AudioAnalysisState()
choreography_recommendation = None
llm_provider = "anthropic"  # or "ollama", "huggingface"
is_analyzing = False
is_generating = False
user_feedback = ""  # For RL training
previous_choreography = None  # Store for feedback comparison
selected_move_index = -1
recent_audio_files = []  # Cache of recent audio files from Downloads
is_playing_audio = False  # Audio playback status
audio_initialized = False  # Pygame mixer initialization status

def load_moves():
    """Load moves from moves.json."""
    global dances, emotions
    try:
        with open('moves.json', 'r') as f:
            moves_data = json.load(f)
            dances = sorted(moves_data.get('dances', []))
            emotions = sorted(moves_data.get('emotions', []))
            print(f"Loaded {len(dances)} dances and {len(emotions)} emotions")
            return True
    except Exception as e:
        print(f"Failed to load moves.json: {e}")
        dances = []
        emotions = []
        return False

dances = []
emotions = []

def load_moves():
    """Load the list of available moves from the moves.json manifest."""
    global dances, emotions
    try:
        with open('moves.json', 'r') as f:
            move_data = json.load(f)
            # Extract just the names from the new {name, description} structure
            dances_raw = move_data.get('dances', [])
            emotions_raw = move_data.get('emotions', [])

            # Handle both old (string array) and new (object array) formats
            dances = [m['name'] if isinstance(m, dict) else m for m in dances_raw]
            emotions = [m['name'] if isinstance(m, dict) else m for m in emotions_raw]

        print(f"Loaded {len(dances)} dances and {len(emotions)} emotions from manifest.")
    except Exception as e:
        print(f"Error loading moves.json: {e}")


def check_daemon_connection():
    """Check if daemon is responding."""
    global daemon_connected
    try:
        response = requests.get(f"{daemon_url}/api/daemon/status", timeout=1)
        daemon_connected = response.status_code == 200
        return daemon_connected
    except:
        daemon_connected = False
        return False

def on_ws_message(ws, message):
    """Handle incoming WebSocket state messages."""
    global latest_daemon_state
    try:
        state = json.loads(message)
        with state_lock:
            latest_daemon_state = state
    except Exception as e:
        print(f"WebSocket message error: {e}")

def on_ws_error(ws, error):
    """Handle WebSocket errors."""
    print(f"WebSocket error: {error}")

def on_ws_close(ws, close_status_code, close_msg):
    """Handle WebSocket close."""
    print(f"WebSocket closed: {close_status_code} - {close_msg}")

def on_ws_open(ws):
    """Handle WebSocket connection opened."""
    print("WebSocket connected to daemon state stream")

def start_state_websocket():
    """Start WebSocket connection in background thread."""
    global ws_app, ws_thread

    ws_url = f"ws://localhost:8100/api/state/ws/full?with_head_joints=true&with_antenna_positions=true&with_body_yaw=true&frequency=30"

    ws_app = WebSocketApp(
        ws_url,
        on_message=on_ws_message,
        on_error=on_ws_error,
        on_close=on_ws_close,
        on_open=on_ws_open
    )

    ws_thread = threading.Thread(target=ws_app.run_forever, daemon=True)
    ws_thread.start()

def get_latest_daemon_state():
    """Get the latest state from WebSocket (thread-safe)."""
    with state_lock:
        return latest_daemon_state

def execute_move(dataset, move_name):
    """Execute a move via daemon API with breathing coordination."""
    global last_move_uuid, status_message, status_message_time
    try:
        url = f"{daemon_url}/api/move/play/recorded-move-dataset/{dataset}/{move_name}"
        response = requests.post(url, timeout=2)
        if response.status_code == 200:
            data = response.json()
            last_move_uuid = data.get('uuid')
            status_message = f"✓ Executing: {move_name}"
            status_message_time = time.time()
            print(f"Executing {dataset}/{move_name} (UUID: {last_move_uuid})")
            return True
        else:
            status_message = f"✗ Failed: {response.status_code}"
            status_message_time = time.time()
            return False
    except Exception as e:
        status_message = f"✗ Error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Failed to execute move: {e}")
        return False

def validate_yaw_step_lock(head_yaw_deg, body_yaw_deg):
    """
    Validate that head-body yaw difference is within safe limits.

    Args:
        head_yaw_deg: Head yaw angle in degrees
        body_yaw_deg: Body yaw angle in degrees

    Returns:
        bool: True if within ±65° limit
    """
    difference = abs(head_yaw_deg - body_yaw_deg)
    return difference <= MAX_YAW_DIFF_DEG

def update_manual_controls_from_state():
    """Update manual control values from current daemon state."""
    global manual_x, manual_y, manual_z, manual_yaw, manual_pitch, manual_roll
    global manual_body_yaw, manual_left_antenna, manual_right_antenna

    daemon_state = get_latest_daemon_state()
    if not daemon_state:
        return False

    try:
        # Get head pose if available (convert from radians to degrees)
        head_pose = daemon_state.get('head_pose')
        if head_pose:
            manual_x = head_pose.get('x', 0.0)
            manual_y = head_pose.get('y', 0.0)
            manual_z = head_pose.get('z', 0.0)
            manual_yaw = np.degrees(head_pose.get('yaw', 0.0))
            manual_pitch = np.degrees(head_pose.get('pitch', 0.0))
            manual_roll = np.degrees(head_pose.get('roll', 0.0))
            print(f"Sync from daemon - Head yaw: {manual_yaw:.1f}°")

        # Get body yaw if available (convert from radians to degrees)
        if 'body_yaw' in daemon_state:
            manual_body_yaw = np.degrees(daemon_state.get('body_yaw', 0.0))
            print(f"Sync from daemon - Body yaw: {manual_body_yaw:.1f}°")

        # Get antenna positions if available
        antenna_pos = daemon_state.get('antennas_position', [])
        if antenna_pos and len(antenna_pos) == 2:
            manual_left_antenna = antenna_pos[0]
            manual_right_antenna = antenna_pos[1]

        return True
    except Exception as e:
        print(f"Error updating manual controls: {e}")
        return False

def send_manual_position():
    """Send manual position to daemon via goto endpoint with step lock validation and breathing coordination."""
    global status_message, status_message_time, last_move_uuid
    try:
        # Validate yaw step lock before sending (fail-safe check)
        is_valid = validate_yaw_step_lock(manual_yaw, manual_body_yaw)

        if not is_valid:
            # This should never happen with UI constraints, but check anyway
            diff = abs(manual_yaw - manual_body_yaw)
            status_message = f"✗ YAW UNSAFE: {diff:.1f}° (max 65°)"
            status_message_time = time.time()
            print(f"SAFETY FAIL-SAFE: Head-body yaw difference {diff:.1f}° exceeds 65° limit!")
            return False

        # Signal conversation app to pause breathing
        try:
            requests.post(f"{conversation_app_url}/api/external_control/start", timeout=0.5)
            print("Breathing paused for manual position")
        except:
            print("Warning: Could not pause breathing (conversation app not running)")

        url = f"{daemon_url}/api/move/goto"
        payload = {
            "head_pose": {
                "x": float(manual_x),
                "y": float(manual_y),
                "z": float(manual_z),
                "roll": np.radians(float(manual_roll)),
                "pitch": np.radians(float(manual_pitch)),
                "yaw": np.radians(float(manual_yaw))
            },
            "antennas": [float(manual_left_antenna), float(manual_right_antenna)],
            "body_yaw": np.radians(float(manual_body_yaw)),
            "duration": float(manual_duration)
        }
        print(f"Sending to daemon - Head yaw: {manual_yaw:.1f}° ({np.radians(manual_yaw):.3f} rad), Body yaw: {manual_body_yaw:.1f}° ({np.radians(manual_body_yaw):.3f} rad)")
        response = requests.post(url, json=payload, timeout=2)
        if response.status_code == 200:
            result = response.json()
            last_move_uuid = result.get("uuid")
            status_message = "✓ Position sent"
            status_message_time = time.time()
            print(f"Manual position sent: {manual_x:.2f}, {manual_y:.2f}, {manual_z:.2f}")
            return True
        else:
            status_message = f"✗ Position failed: {response.status_code}"
            status_message_time = time.time()
            return False
    except Exception as e:
        status_message = f"✗ Position error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Error sending manual position: {e}")
        return False
    finally:
        # Resume breathing after manual position completes
        try:
            requests.post(f"{conversation_app_url}/api/external_control/stop", timeout=0.5)
            print("Breathing resumed after manual position")
        except:
            pass

def stop_move():
    """Stop current move via daemon API and resume breathing."""
    global status_message, status_message_time, last_move_uuid
    if not last_move_uuid:
        status_message = "No move to stop"
        status_message_time = time.time()
        return
    try:
        url = f"{daemon_url}/api/move/stop"
        response = requests.post(url, json={"uuid": last_move_uuid}, timeout=2)
        if response.status_code == 200:
            status_message = "✓ Stopped"
            status_message_time = time.time()
            print(f"Stopped move {last_move_uuid}")
            last_move_uuid = None

            return True
        else:
            status_message = f"✗ Stop failed: {response.status_code}"
            status_message_time = time.time()
            return False
    except Exception as e:
        status_message = f"✗ Stop error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Failed to stop move: {e}")
        return False

def toggle_fullscreen(window):
    """Toggle between fullscreen and windowed mode."""
    global is_fullscreen, windowed_width, windowed_height, windowed_xpos, windowed_ypos

    if is_fullscreen:
        # Switch to windowed mode
        glfw.set_window_monitor(window, None, windowed_xpos, windowed_ypos,
                                windowed_width, windowed_height, 0)
        is_fullscreen = False
        print("Switched to windowed mode")
    else:
        # Save current window position and size
        windowed_xpos, windowed_ypos = glfw.get_window_pos(window)
        windowed_width, windowed_height = glfw.get_window_size(window)

        # Get primary monitor and its video mode
        monitor = glfw.get_primary_monitor()
        mode = glfw.get_video_mode(monitor)

        # Switch to fullscreen
        glfw.set_window_monitor(window, monitor, 0, 0, mode.size.width, mode.size.height, mode.refresh_rate)
        is_fullscreen = True
        print("Switched to fullscreen mode")

def keyboard(window, key, scancode, act, mods):
    global panel_collapsed
    if act == glfw.PRESS:
        if key == glfw.KEY_BACKSPACE:
            if backend:
                mujoco.mj_resetData(model, data)
                sleep_qpos = backend._SLEEP_HEAD_JOINT_POSITIONS + backend._SLEEP_ANTENNAS_JOINT_POSITIONS
                data.qpos[backend.joint_qpos_addr] = np.array(sleep_qpos).reshape(-1, 1)
                mujoco.mj_forward(model, data)
        elif key == glfw.KEY_F11:
            toggle_fullscreen(window)
        elif key == glfw.KEY_TAB:
            panel_collapsed = not panel_collapsed
            print(f"Control panel: {'collapsed' if panel_collapsed else 'expanded'}")

def mouse_button(window, button, act, mods):
    global button_left, button_middle, button_right, lastx, lasty
    button_left = (glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_LEFT) == glfw.PRESS)
    button_middle = (glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_MIDDLE) == glfw.PRESS)
    button_right = (glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_RIGHT) == glfw.PRESS)
    lastx, lasty = glfw.get_cursor_pos(window)

def mouse_move(window, xpos, ypos):
    # Do not handle mouse movement if ImGui wants to capture it
    if imgui.get_io().want_capture_mouse:
        return

    global lastx, lasty, button_left, button_middle, button_right
    dx = xpos - lastx
    dy = ypos - lasty
    lastx = xpos
    lasty = ypos
    if not (button_left or button_middle or button_right):
        return
    width, height = glfw.get_window_size(window)
    mod_shift = (glfw.get_key(window, glfw.KEY_LEFT_SHIFT) == glfw.PRESS or
                 glfw.get_key(window, glfw.KEY_RIGHT_SHIFT) == glfw.PRESS)
    # Left-click = orbit, Right-click = pan, Middle-click = zoom
    action_orbit = button_left
    action_pan = button_right
    action_zoom = button_middle
    if action_orbit:
        mujoco.mjv_moveCamera(model, mujoco.mjtMouse.mjMOUSE_ROTATE_V, 0, dy / height, scn, cam)
        mujoco.mjv_moveCamera(model, mujoco.mjtMouse.mjMOUSE_ROTATE_H, dx / height, 0, scn, cam)
    elif action_pan:
        mujoco.mjv_moveCamera(model, mujoco.mjtMouse.mjMOUSE_MOVE_V, 0, dy / height, scn, cam)
        mujoco.mjv_moveCamera(model, mujoco.mjtMouse.mjMOUSE_MOVE_H, dx / height, 0, scn, cam)  # Inverted horizontal
    elif action_zoom:
        mujoco.mjv_moveCamera(model, mujoco.mjtMouse.mjMOUSE_ZOOM, 0, dy / height, scn, cam)

def scroll(window, xoffset, yoffset):
    if imgui.get_io().want_capture_mouse:
        return
    mujoco.mjv_moveCamera(model, mujoco.mjtMouse.mjMOUSE_ZOOM, 0, -0.05 * yoffset, scn, cam)

def get_recent_audio_files(limit=5):
    """Get most recent audio files from Downloads folder."""
    try:
        downloads_path = os.path.expanduser("~/Downloads")
        audio_extensions = ('.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.aiff', '.aif')

        # Get all audio files with modification time
        audio_files = []
        for filename in os.listdir(downloads_path):
            if filename.lower().endswith(audio_extensions):
                full_path = os.path.join(downloads_path, filename)
                try:
                    mtime = os.path.getmtime(full_path)
                    audio_files.append((full_path, mtime, filename))
                except:
                    continue

        # Sort by modification time (newest first) and take top N
        audio_files.sort(key=lambda x: x[1], reverse=True)
        return [(path, name) for path, mtime, name in audio_files[:limit]]
    except Exception as e:
        print(f"Error scanning Downloads: {e}")
        return []

def import_audio_file():
    """Open file dialog to select audio file using osascript (works on all macOS versions)."""
    try:
        # Use AppleScript to show file picker (compatible with older macOS)
        script = '''
        tell application "System Events"
            activate
            set theFile to choose file with prompt "Select Audio File" of type {"public.audio"}
            return POSIX path of theFile
        end tell
        '''

        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            file_path = result.stdout.strip()
            if file_path:
                audio_state.audio_path = file_path
                print(f"Selected audio: {audio_state.audio_path}")
                return True
        return False
    except subprocess.TimeoutExpired:
        print("File selection cancelled (timeout)")
        return False
    except Exception as e:
        print(f"Error importing audio: {e}")
        return False

def analyze_audio():
    """Analyze imported audio file by calling the standalone analysis script."""
    global is_analyzing, status_message, status_message_time

    if not audio_state.audio_path:
        status_message = "✗ No audio file selected"
        status_message_time = time.time()
        return

    try:
        is_analyzing = True
        status_message = "Analyzing audio..."
        status_message_time = time.time()

        # Command to run the standalone analysis script
        command = [sys.executable, "run_essentia_analysis.py", audio_state.audio_path]

        # Execute the script as a subprocess
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True  # This will raise a CalledProcessError if the script returns a non-zero exit code
        )

        # Parse the JSON output from the script's stdout
        analysis_result = json.loads(result.stdout)

        is_analyzing = False
        if analysis_result:
            audio_state.set_analysis(analysis_result)
            status_message = f"✓ Analysis complete: {audio_state.analysis['bpm']:.1f} BPM, {audio_state.analysis['duration']:.1f}s"
            status_message_time = time.time()
            print(f"Audio analysis complete: {audio_state.analysis['bpm']:.1f} BPM")
        else:
            status_message = "✗ Analysis failed"
            status_message_time = time.time()

    except subprocess.CalledProcessError as e:
        is_analyzing = False
        error_output = e.stderr.strip()
        status_message = f"✗ Analysis error: {error_output[:50]}"
        status_message_time = time.time()
        print(f"Error analyzing audio via subprocess: {error_output}")
    except Exception as e:
        is_analyzing = False
        status_message = f"✗ Analysis error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Error processing analysis result: {e}")

def generate_choreography():
    """Generate choreography recommendation using LLM."""
    global choreography_recommendation, is_generating, status_message, status_message_time

    if not audio_state.analysis:
        status_message = "✗ Analyze audio first"
        status_message_time = time.time()
        return

    try:
        is_generating = True
        status_message = "Generating choreography with ReAct agent..."
        status_message_time = time.time()

        # Generate choreography using ReAct agent with tool-based reasoning
        agent = ReActChoreographer(audio_state.analysis, max_iterations=20)
        choreography_recommendation = agent.generate()

        is_generating = False
        if choreography_recommendation:
            # Save the generated choreography
            save_path = f"responses/choreography_recommendations/rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'w') as f:
                json.dump(choreography_recommendation, f, indent=2)

            # Support both old ('choreography') and new ('sequence') formats
            moves = choreography_recommendation.get('sequence', choreography_recommendation.get('choreography', []))
            move_count = len(moves)
            status_message = f"✓ Generated {move_count} moves"
            status_message_time = time.time()
            print(f"Choreography generated: {move_count} moves")
        else:
            status_message = "✗ Generation failed"
            status_message_time = time.time()

    except Exception as e:
        is_generating = False
        status_message = f"✗ Generation error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Error generating choreography: {e}")

def generate_choreography_with_feedback(feedback_text):
    """Regenerate choreography using user feedback for RL training."""
    global choreography_recommendation, previous_choreography, is_generating, status_message, status_message_time

    if not audio_state.analysis:
        status_message = "✗ Analyze audio first"
        status_message_time = time.time()
        return

    if not feedback_text.strip():
        status_message = "✗ Enter feedback first"
        status_message_time = time.time()
        return

    try:
        is_generating = True
        status_message = "Regenerating with feedback..."
        status_message_time = time.time()

        # Store feedback for RL training
        feedback_entry = {
            'timestamp': datetime.now().isoformat(),
            'audio_file': audio_state.audio_path,
            'audio_features': {
                'bpm': audio_state.analysis['bpm'],
                'energy': audio_state.analysis['energy'],
                'danceability': audio_state.analysis['danceability'],
                'duration': audio_state.analysis['duration']
            },
            'previous_choreography': choreography_recommendation,
            'user_feedback': feedback_text,
            'feedback_type': 'negative'  # Assume feedback is critique
        }

        # Save feedback to RL training dataset
        feedback_dir = "responses/choreography_feedback"
        os.makedirs(feedback_dir, exist_ok=True)
        feedback_path = f"{feedback_dir}/feedback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(feedback_path, 'w') as f:
            json.dump(feedback_entry, f, indent=2)
        print(f"[RL] Feedback saved to {feedback_path}")

        # TODO: In future, use feedback to modify LLM prompt
        # For now, regenerate with enhanced prompt

        # Store previous for comparison
        previous_choreography = choreography_recommendation.copy() if choreography_recommendation else None

        # Regenerate using ReAct agent
        # TODO: Incorporate feedback into agent prompt in future
        save_path = f"responses/choreography_recommendations/rec_feedback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        agent = ReActChoreographer(audio_state.analysis, max_iterations=20)
        choreography_recommendation = agent.generate()

        # Save the result
        if choreography_recommendation:
            with open(save_path, 'w') as f:
                json.dump(choreography_recommendation, f, indent=2)

        is_generating = False
        if choreography_recommendation:
            moves = choreography_recommendation.get('sequence', [])
            status_message = f"✓ Regenerated: {len(moves)} moves"
            status_message_time = time.time()
            print(f"[RL] Choreography regenerated based on feedback")
        else:
            status_message = "✗ Regeneration failed"
            status_message_time = time.time()

    except Exception as e:
        is_generating = False
        status_message = f"✗ Error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Error in feedback-based generation: {e}")

def export_final_choreography():
    """Export final edited choreography to JSON."""
    global status_message, status_message_time

    if not choreography_recommendation:
        status_message = "✗ No choreography to export"
        status_message_time = time.time()
        return

    try:
        # Prepare export data
        audio_filename = os.path.basename(audio_state.audio_path) if audio_state.audio_path else "unknown"
        audio_filename_no_ext = os.path.splitext(audio_filename)[0]

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        export_path = f"choreographies/{audio_filename_no_ext}_final_{timestamp}.json"

        # Create export directory
        os.makedirs("choreographies", exist_ok=True)

        # Export in official Reachy choreography format
        final_choreo = {
            "bpm": choreography_recommendation.get('bpm'),
            "sequence": choreography_recommendation.get('sequence', choreography_recommendation.get('choreography', []))
        }

        # Save
        with open(export_path, 'w') as f:
            json.dump(final_choreo, f, indent=2)

        status_message = f"✓ Exported to {os.path.basename(export_path)}"
        status_message_time = time.time()
        print(f"Choreography exported to {export_path}")

    except Exception as e:
        status_message = f"✗ Export error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Error exporting choreography: {e}")

def init_audio():
    """Initialize pygame audio mixer."""
    global audio_initialized
    try:
        if not audio_initialized:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            audio_initialized = True
            print("Audio mixer initialized")
        return True
    except Exception as e:
        print(f"Failed to initialize audio: {e}")
        return False

def play_choreography_with_audio():
    """Play choreography synchronized with audio using the ReachyMini SDK."""
    global is_playing_audio, status_message, status_message_time

    if not audio_state.audio_path:
        status_message = "✗ No audio file"
        status_message_time = time.time()
        return

    if not choreography_recommendation:
        status_message = "✗ No choreography"
        status_message_time = time.time()
        return

    if not daemon_connected or not reachy or not dances_library:
        status_message = "✗ SDK not initialized"
        status_message_time = time.time()
        return

    try:
        if not init_audio():
            status_message = "✗ Audio init failed"
            status_message_time = time.time()
            return

        is_playing_audio = True
        status_message = "▶ Playing..."
        status_message_time = time.time()

        # Create a temporary file for the choreography JSON
        temp_choreography_path = "/tmp/current_choreography.json"
        with open(temp_choreography_path, 'w') as f:
            json.dump(choreography_recommendation, f)

            # The main playback logic
            def playback():
                global is_playing_audio, status_message, status_message_time, current_choreo_move_info
                try:
                    # Create a temporary file for the choreography JSON
                    temp_choreography_path = "/tmp/current_choreography.json"
                    with open(temp_choreography_path, 'w') as f:
                        json.dump(choreography_recommendation, f)

                    # Load the choreography from the temp file
                    choreo_move = Choreography(temp_choreography_path, dances_library, emotions_library)

                    # Load and play audio
                    pygame.mixer.music.load(audio_state.audio_path)

                    # TIMING INSTRUMENTATION
                    audio_start_time = time.time()
                    pygame.mixer.music.play()
                    print(f"[TIMING] Audio started at T=0.000s")
                    print(f"[TIMING] Audio duration: {audio_state.analysis['duration']:.3f}s")
                    print(f"[TIMING] Choreography total duration: {choreo_move.duration:.3f}s")
                    print(f"[TIMING] Total moves: {len(choreo_move.moves)}")
                    print("🎵 Audio started, playing choreography via SDK...")

                    # Thread to update current move info in UI and log timing
                    stop_progress_updater = threading.Event()
                    last_logged_move = -1

                    def progress_updater():
                        nonlocal last_logged_move
                        global current_choreo_move_info
                        start_time = time.time()
                        while not stop_progress_updater.is_set():
                            elapsed_time = time.time() - start_time
                            move_name, move_idx, _, _ = choreo_move.get_move_at_time(elapsed_time)
                            if move_idx != -1:
                                current_choreo_move_info = f"Move {move_idx+1}/{len(choreo_move.moves)}: {move_name}"
                                # Log when we transition to a new move
                                if move_idx != last_logged_move:
                                    print(f"[TIMING] T={elapsed_time:.3f}s - Move {move_idx+1}/{len(choreo_move.moves)}: {move_name}")
                                    last_logged_move = move_idx
                            else:
                                current_choreo_move_info = ""
                            time.sleep(0.1) # Update every 100ms

                    progress_thread = threading.Thread(target=progress_updater, daemon=True)
                    progress_thread.start()

                    # Play the entire choreography as a single move (blocking call)
                    choreo_start_time = time.time()
                    print(f"[TIMING] Choreography SDK play_move() called at T={choreo_start_time - audio_start_time:.3f}s")

                    reachy.play_move(choreo_move)

                    choreo_end_time = time.time()
                    audio_end_time = time.time()

                    stop_progress_updater.set() # Signal updater to stop
                    progress_thread.join() # Wait for updater to finish

                    # TIMING SUMMARY
                    total_elapsed = audio_end_time - audio_start_time
                    choreo_duration = choreo_end_time - choreo_start_time
                    print(f"[TIMING] ✓ Choreography execution complete")
                    print(f"[TIMING] Total elapsed time: {total_elapsed:.3f}s")
                    print(f"[TIMING] Choreography actual duration: {choreo_duration:.3f}s")
                    print(f"[TIMING] Audio still playing: {pygame.mixer.music.get_busy()}")

                    status_message = "✓ Playback complete"

                except Exception as e:
                    print(f"Error during choreography playback: {e}")
                    status_message = f"✗ Playback error: {str(e)[:30]}"
                finally:
                    is_playing_audio = False
                    status_message_time = time.time()
                    current_choreo_move_info = "" # Clear info after playback

            # Run playback in a background thread so the UI doesn't freeze
            threading.Thread(target=playback, daemon=True).start()

    except Exception as e:
        is_playing_audio = False
        status_message = f"✗ Playback error: {str(e)[:30]}"
        status_message_time = time.time()
        print(f"Error starting playback: {e}")

def stop_audio_playback():
    """Stop audio playback and choreography."""
    global is_playing_audio, status_message, status_message_time
    try:
        if audio_initialized and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()

        # Also stop any running moves
        stop_move()

        is_playing_audio = False
        status_message = "■ Stopped"
        status_message_time = time.time()
        print("Stopped audio and choreography")
    except Exception as e:
        print(f"Error stopping playback: {e}")


def initialize_sdk_in_background():
    """Initialize the ReachySDK and move libraries in a background thread."""
    global reachy, dances_library, emotions_library, sdk_initialized

    try:
        reachy = ReachyMini(media_backend="no_media")
        dances_library = RecordedMoves('pollen-robotics/reachy-mini-dances-library')
        emotions_library = RecordedMoves('pollen-robotics/reachy-mini-emotions-library')
        sdk_initialized = True
        print("✓ ReachySDK and Move Libraries initialized from Hugging Face.")
    except Exception as e:
        print(f"✗ Failed to initialize ReachySDK: {e}")
        sdk_initialized = False


def main():
    global backend, model, data, cam, opt, scn, ctx, daemon_connected, bind_antennas

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='MuJoCo Desktop Viewer for Reachy Mini')
    parser.add_argument('--scene', type=str, default='empty',
                        help='Scene to load (empty, minimal, or custom scene name). Default: empty')
    parser.add_argument('--fullscreen', action='store_true',
                        help='Start in fullscreen mode')
    args = parser.parse_args()

    try:
        print(f"Initializing MujocoBackend with scene: {args.scene}")
        backend = MujocoBackend(headless=True, scene=args.scene)
        model = backend.model
        data = backend.data
    except Exception as e:
        print(f"Failed to initialize backend: {e}")
        return

    if not glfw.init():
        return

    # Don't set OpenGL version hints - let GLFW choose the best available
    # MuJoCo requires legacy OpenGL features (ARB_framebuffer_object)
    # that are not available in Core Profile on macOS

    # Create window (fullscreen or windowed)
    global is_fullscreen
    if args.fullscreen:
        monitor = glfw.get_primary_monitor()
        mode = glfw.get_video_mode(monitor)
        window = glfw.create_window(mode.size.width, mode.size.height,
                                     "MuJoCo Desktop Viewer", monitor, None)
        is_fullscreen = True
        print("Started in fullscreen mode (press F11 to toggle)")
    else:
        window = glfw.create_window(1200, 900, "MuJoCo Desktop Viewer", None, None)
        print("Started in windowed mode (press F11 to toggle fullscreen)")
    if not window:
        glfw.terminate()
        return
    glfw.make_context_current(window)
    glfw.swap_interval(1)

    # --- Initialize ImGui ---
    imgui.create_context()
    impl = GlfwFixedRenderer(window, attach_callbacks=False)  # We handle callbacks manually

    mujoco.mjv_defaultCamera(cam)
    mujoco.mjv_defaultOption(opt)
    scn = mujoco.MjvScene(model, maxgeom=10000)
    ctx = mujoco.MjrContext(model, mujoco.mjtFontScale.mjFONTSCALE_150)

    cam.azimuth = 160
    cam.elevation = -20
    cam.distance = 2.0
    cam.lookat = np.array([0.0, 0.0, 0.15])

    glfw.set_key_callback(window, keyboard)
    glfw.set_cursor_pos_callback(window, mouse_move)
    glfw.set_mouse_button_callback(window, mouse_button)
    glfw.set_scroll_callback(window, scroll)

    # Initialize daemon connection and load moves
    print("Loading moves from moves.json...")
    load_moves()
    print("Checking daemon connection...")
    check_daemon_connection()

    if daemon_connected:
        print("✓ Connected to daemon")
        print("Starting WebSocket state stream...")
        start_state_websocket()
        
        # Start SDK initialization in the background
        print("Initializing SDK in background...")
        threading.Thread(target=initialize_sdk_in_background, daemon=True).start()

    else:
        print("✗ Daemon not running (start it to enable move controls)")

    sim_step = 0

    while not glfw.window_should_close(window):
        loop_start = time.time()

        # Get latest state from WebSocket (non-blocking)
        if daemon_connected:
            daemon_state = get_latest_daemon_state()
            if daemon_state:
                # Get target positions from daemon
                head_joints = daemon_state.get('head_joints', [])
                antenna_positions = daemon_state.get('antennas_position', [])

                if head_joints and len(head_joints) == 7:
                    # Set actuator controls (motors), not joint positions
                    # This respects Stewart platform constraints
                    data.ctrl[:7] = head_joints

                if antenna_positions and len(antenna_positions) == 2:
                    # Antenna actuators (daemon sends [left, right], MuJoCo expects negated)
                    data.ctrl[-2:] = -np.array(antenna_positions)

                body_yaw = daemon_state.get('body_yaw')
                if body_yaw is not None:
                    data.ctrl[0] = body_yaw

        # Run multiple physics steps to make tracking more responsive
        for _ in range(5):  # Run 5 sub-steps per frame for faster convergence
            mujoco.mj_step(model, data)

        sim_step += 1

        # --- ImGui Frame ---
        impl.process_inputs()
        imgui.new_frame()

        # Control panel (always show window, but can collapse)
        global selected_dance, selected_emotion, selected_dataset
        global status_message, status_message_time, panel_collapsed

        # Get window size to position panel on right side
        window_width, window_height = glfw.get_window_size(window)
        panel_width = 450
        panel_height = window_height - 20  # Almost full screen height with small margin

        imgui.set_next_window_size(panel_width, panel_height, imgui.ALWAYS)
        imgui.set_next_window_position(window_width - panel_width - 10, 10, imgui.ALWAYS)
        if panel_collapsed:
            imgui.set_next_window_collapsed(True, imgui.ONCE)

        expanded, _ = imgui.begin("Reachy Mini Control Panel", True)

        if expanded:
            # Title with inline hint
            imgui.text("Reachy Mini Control Panel")
            imgui.same_line()
            imgui.push_style_var(imgui.STYLE_ALPHA, 0.6)
            imgui.set_window_font_scale(0.85)
            imgui.text("(TAB quick hide/show)")
            imgui.set_window_font_scale(1.0)
            imgui.pop_style_var()
            imgui.separator()

            # === VIEWER CONTROLS ===
            if imgui.collapsing_header("Viewer Controls", imgui.TREE_NODE_DEFAULT_OPEN)[0]:
                if imgui.button("Toggle Fullscreen (F11)", 180, 30):
                    toggle_fullscreen(window)

                imgui.same_line()
                imgui.text(f"{'Fullscreen' if is_fullscreen else 'Windowed'}")

                imgui.text(f"Scene: {args.scene}")

            imgui.separator()

            # === DAEMON CONNECTION ===
            if imgui.collapsing_header("Daemon Connection", imgui.TREE_NODE_DEFAULT_OPEN)[0]:
                # Connection status
                if daemon_connected:
                    imgui.text_colored("● Connected", 0.3, 1.0, 0.3)
                else:
                    imgui.text_colored("● Disconnected", 1.0, 0.3, 0.3)

                imgui.same_line()
                if imgui.button("Reconnect"):
                    check_daemon_connection()

                imgui.text(f"URL: {daemon_url}")

            imgui.separator()

            # === MANUAL POSITION CONTROL ===
            if daemon_connected:
                if not sdk_initialized:
                    imgui.text_colored("Initializing SDK and loading moves...", 1.0, 1.0, 0.3)
                elif imgui.collapsing_header("Manual Position Control")[0]:
                    global manual_x, manual_y, manual_z, manual_yaw, manual_pitch, manual_roll
                    global manual_body_yaw, manual_left_antenna, manual_right_antenna, manual_duration
                    global bind_yaw

                    imgui.text_colored("Head Position (meters):", 0.3, 1.0, 1.0)

                    # X position
                    changed, manual_x = imgui.slider_float("X##pos", manual_x, -0.025, 0.025, "%.3f")

                    # Y position
                    changed, manual_y = imgui.slider_float("Y##pos", manual_y, -0.025, 0.025, "%.3f")

                    # Z position (realistic range for Reachy's neck)
                    changed, manual_z = imgui.slider_float("Z##pos", manual_z, -0.025, 0.023, "%.3f")

                    imgui.spacing()
                    imgui.text_colored("Head Rotation (degrees):", 0.3, 1.0, 1.0)

                    # Head Yaw - full range
                    changed_head_yaw, new_head_yaw = imgui.slider_float(
                        "Head Yaw##rot",
                        manual_yaw,
                        -HEAD_YAW_LIMIT,
                        HEAD_YAW_LIMIT,
                        "%.0f°"
                    )

                    # Pitch (±40° from docs)
                    changed, manual_pitch = imgui.slider_float("Pitch##rot", manual_pitch, -40.0, 40.0, "%.0f°")

                    # Roll (±40° from docs)
                    changed, manual_roll = imgui.slider_float("Roll##rot", manual_roll, -40.0, 40.0, "%.0f°")

                    imgui.spacing()
                    imgui.text_colored("Body Rotation (degrees):", 0.3, 1.0, 1.0)

                    # Body yaw - full range
                    changed_body_yaw, new_body_yaw = imgui.slider_float(
                        "Body Yaw##bodyrot",
                        manual_body_yaw,
                        -BODY_YAW_LIMIT,
                        BODY_YAW_LIMIT,
                        "%.0f°"
                    )

                    # Handle slider changes with sync lock and safety auto-follow
                    if bind_yaw:
                        # SYNC LOCK ON: Intentional synchronized movement
                        if changed_head_yaw:
                            manual_yaw = new_head_yaw
                            manual_body_yaw = new_head_yaw  # Move together
                            # Clamp body to its limits
                            manual_body_yaw = max(-BODY_YAW_LIMIT, min(BODY_YAW_LIMIT, manual_body_yaw))
                        elif changed_body_yaw:
                            manual_body_yaw = new_body_yaw
                            manual_yaw = new_body_yaw  # Move together
                            # Clamp head to its limits
                            manual_yaw = max(-HEAD_YAW_LIMIT, min(HEAD_YAW_LIMIT, manual_yaw))
                    else:
                        # SYNC LOCK OFF: Independent movement with automatic safety following
                        if changed_head_yaw:
                            manual_yaw = new_head_yaw
                            # Check if this creates unsafe difference
                            diff = abs(manual_yaw - manual_body_yaw)
                            if diff > MAX_YAW_DIFF_DEG:
                                # Auto-follow body yaw to maintain safety
                                if manual_yaw > manual_body_yaw:
                                    manual_body_yaw = manual_yaw - MAX_YAW_DIFF_DEG
                                else:
                                    manual_body_yaw = manual_yaw + MAX_YAW_DIFF_DEG
                                # Clamp to body limits
                                manual_body_yaw = max(-BODY_YAW_LIMIT, min(BODY_YAW_LIMIT, manual_body_yaw))

                        if changed_body_yaw:
                            manual_body_yaw = new_body_yaw
                            # Check if this creates unsafe difference
                            diff = abs(manual_yaw - manual_body_yaw)
                            if diff > MAX_YAW_DIFF_DEG:
                                # Auto-follow head yaw to maintain safety
                                if manual_body_yaw > manual_yaw:
                                    manual_yaw = manual_body_yaw - MAX_YAW_DIFF_DEG
                                else:
                                    manual_yaw = manual_body_yaw + MAX_YAW_DIFF_DEG
                                # Clamp to head limits
                                manual_yaw = max(-HEAD_YAW_LIMIT, min(HEAD_YAW_LIMIT, manual_yaw))

                    # Display current yaw difference with color coding
                    yaw_diff = abs(manual_yaw - manual_body_yaw)
                    imgui.spacing()
                    if yaw_diff <= MAX_YAW_DIFF_DEG:
                        imgui.text_colored(f"✓ Yaw Difference: {yaw_diff:.1f}° / {MAX_YAW_DIFF_DEG:.0f}°", 0.3, 1.0, 0.3)
                    else:
                        # This should never happen due to auto-follow
                        imgui.text_colored(f"✗ UNSAFE: {yaw_diff:.1f}° / {MAX_YAW_DIFF_DEG:.0f}°", 1.0, 0.0, 0.0)

                    # Sync lock checkbox
                    imgui.spacing()
                    changed_bind, bind_yaw = imgui.checkbox("Sync Lock (move head+body together)", bind_yaw)

                    imgui.spacing()
                    imgui.text_colored("Antennas (radians):", 0.3, 1.0, 1.0)

                    # Left antenna
                    changed_left, manual_left_antenna = imgui.slider_float("Left Ant##ant", manual_left_antenna, -3.0, 3.0, "%.1f")

                    # Right antenna
                    changed_right, manual_right_antenna = imgui.slider_float("Right Ant##ant", manual_right_antenna, -3.0, 3.0, "%.1f")

                    if bind_antennas:
                        if changed_left:
                            manual_right_antenna = -manual_left_antenna
                        elif changed_right:
                            manual_left_antenna = -manual_right_antenna

                    # Sync lock checkbox
                    imgui.spacing()
                    changed_bind, bind_antennas = imgui.checkbox("Sync Lock##antennas", bind_antennas)

                    imgui.spacing()
                    imgui.text_colored("Movement Duration:", 0.3, 1.0, 1.0)

                    # Duration slider
                    changed, manual_duration = imgui.slider_float("Duration (s)##duration", manual_duration, 0.05, 3.0, "%.1f")

                    imgui.spacing()

                    # Send button
                    if imgui.button("Apply Position", 120, 35):
                        send_manual_position()
                        status_message = "✓ Position sent"
                        status_message_time = time.time()

                    imgui.same_line()
                    if imgui.button("Sync", 70, 35):
                        if update_manual_controls_from_state():
                            status_message = "✓ Synced with current"
                            status_message_time = time.time()
                        else:
                            status_message = "✗ No state available"
                            status_message_time = time.time()

                    imgui.same_line()
                    if imgui.button("Reset", 70, 35):
                        manual_x = 0.0
                        manual_y = 0.0
                        manual_z = 0.01  # Neutral position
                        manual_yaw = 0.0
                        manual_pitch = 0.0
                        manual_roll = 0.0
                        manual_body_yaw = 0.0  # Reset body yaw too
                        manual_left_antenna = 0.0
                        manual_right_antenna = 0.0

            imgui.separator()

            # === MOVE CONTROL ===
            if daemon_connected and sdk_initialized and (dances or emotions):
                if imgui.collapsing_header("Move Control", imgui.TREE_NODE_DEFAULT_OPEN)[0]:
                    # Dataset selection (dances vs emotions)
                    imgui.text("Dataset:")
                    if imgui.radio_button("Dances", selected_dataset == "dances"):
                        selected_dataset = "dances"

                    imgui.same_line()
                    if imgui.radio_button("Emotions", selected_dataset == "emotions"):
                        selected_dataset = "emotions"

                    imgui.spacing()

                    # Move selection dropdown
                    if selected_dataset == "dances" and dances:
                        imgui.text(f"Select Dance ({len(dances)} available):")
                        changed, selected_dance = imgui.combo(
                            "##dance_select",
                            selected_dance,
                            dances
                        )

                        # Execute button
                        if imgui.button("Execute Dance", 180, 40):
                            execute_move("pollen-robotics/reachy-mini-dances-library",
                                       dances[selected_dance])

                    elif selected_dataset == "emotions" and emotions:
                        imgui.text(f"Select Emotion ({len(emotions)} available):")
                        changed, selected_emotion = imgui.combo(
                            "##emotion_select",
                            selected_emotion,
                            emotions
                        )

                        # Execute button
                        if imgui.button("Execute Emotion", 180, 40):
                            execute_move("pollen-robotics/reachy-mini-emotions-library",
                                       emotions[selected_emotion])

                    # Stop button
                    imgui.same_line()
                    if imgui.button("STOP", 80, 40):
                        stop_move()

                    # Status message
                    if status_message and (time.time() - status_message_time < 3.0):
                        if status_message.startswith("✓"):
                            imgui.text_colored(status_message, 0.3, 1.0, 0.3)
                        else:
                            imgui.text_colored(status_message, 1.0, 0.3, 0.3)

            elif not daemon_connected:
                imgui.text_colored("Connect to daemon to control moves", 1.0, 0.8, 0.3)
            elif not (dances or emotions):
                imgui.text_colored("No moves loaded from moves.json", 1.0, 0.8, 0.3)

            imgui.separator()

        imgui.end()

        # === SEPARATE CHOREOGRAPHY BUILDER WINDOW ===
        choreography_window_width = 500
        choreography_window_height = window_height - 20
        imgui.set_next_window_size(choreography_window_width, choreography_window_height, imgui.ONCE)
        imgui.set_next_window_position(window_width - panel_width - choreography_window_width - 20, 10, imgui.ONCE)

        choreography_expanded, choreography_open = imgui.begin("Choreography Builder", True)

        if choreography_expanded:
            global choreography_recommendation, recent_audio_files, user_feedback
            global llm_provider, is_analyzing, is_generating, selected_move_index

            # Audio import
            imgui.text_colored("Audio File:", 0.3, 1.0, 1.0)
            if audio_state.audio_path:
                audio_filename = os.path.basename(audio_state.audio_path)
                imgui.text(f"{audio_filename[:30]}...")
            else:
                imgui.text("No file selected")

            imgui.same_line()
            if imgui.button("Browse..."):
                import_audio_file()

            # Recent files quick-select
            imgui.spacing()
            imgui.text_colored("Recent Downloads:", 0.8, 0.8, 0.8)

            # Refresh recent files list
            if not recent_audio_files or imgui.button("Refresh", 70, 20):
                recent_audio_files = get_recent_audio_files(5)

            # Show recent files as clickable buttons
            if recent_audio_files:
                for i, (file_path, filename) in enumerate(recent_audio_files):
                    # Truncate long filenames
                    display_name = filename if len(filename) <= 35 else filename[:32] + "..."
                    if imgui.button(f"{display_name}##recent_{i}", 280, 25):
                        audio_state.audio_path = file_path
                        print(f"Selected recent audio: {audio_state.audio_path}")
            else:
                imgui.text_colored("(no recent audio files)", 0.6, 0.6, 0.6)

            # Analysis section
            if audio_state.audio_path and not audio_state.analysis:
                imgui.spacing()
                if is_analyzing:
                    imgui.text_colored("Analyzing...", 1.0, 1.0, 0.3)
                else:
                    if imgui.button("Analyze Audio", 180, 30):
                        # Run analysis in background thread to avoid blocking
                        threading.Thread(target=analyze_audio, daemon=True).start()

            # Display analysis results (read-only access)
            if audio_state.analysis:
                imgui.spacing()
                imgui.separator()
                if imgui.collapsing_header("Audio Analysis Results", imgui.TREE_NODE_DEFAULT_OPEN)[0]:
                    imgui.text_colored("=== AUDIO ANALYSIS ===", 0.3, 1.0, 1.0)

                    # Basic Info
                    imgui.spacing()
                    imgui.text_colored("Basic Info:", 1.0, 1.0, 0.5)
                    imgui.text(f"Duration: {audio_state.analysis['duration']:.1f}s")
                    imgui.text(f"Sample Rate: {audio_state.analysis.get('sample_rate', 44100)} Hz")

                    # Rhythm
                    imgui.spacing()
                    imgui.text_colored("Rhythm:", 1.0, 1.0, 0.5)
                    imgui.text(f"BPM: {audio_state.analysis['bpm']:.1f}")
                    imgui.text(f"Beat Count: {audio_state.analysis.get('beat_count', 0)}")
                    imgui.text(f"Onset Rate: {audio_state.analysis.get('onset_rate', 0):.2f} events/s")

                    # Musical Key
                    imgui.spacing()
                    imgui.text_colored("Key & Scale:", 1.0, 1.0, 0.5)
                    key = audio_state.analysis.get('key', 'Unknown')
                    scale = audio_state.analysis.get('scale', 'Unknown')
                    key_strength = audio_state.analysis.get('key_strength', 0)
                    imgui.text(f"Key: {key} {scale}")
                    imgui.text(f"Confidence: {key_strength:.2f}")

                    # Energy & Dynamics
                    imgui.spacing()
                    imgui.text_colored("Energy & Dynamics:", 1.0, 1.0, 0.5)
                    imgui.text(f"Energy: {audio_state.analysis['energy']:.3f}")
                    imgui.text(f"Loudness: {audio_state.analysis.get('loudness', 0):.2f} dB")
                    imgui.text(f"Dynamic Range: {audio_state.analysis.get('dynamic_complexity', 0):.2f}")

                    # Spectral Features
                    imgui.spacing()
                    imgui.text_colored("Spectral:", 1.0, 1.0, 0.5)
                    spectral = audio_state.analysis.get('spectral', {})
                    imgui.text(f"Brightness: {spectral.get('centroid', 0):.0f} Hz")
                    imgui.text(f"Rolloff: {spectral.get('rolloff', 0):.0f} Hz")
                    imgui.text(f"Flatness: {spectral.get('flatness', 0):.3f}")

                    # Mood Profile
                    imgui.spacing()
                    imgui.text_colored("Mood Profile:", 1.0, 1.0, 0.5)
                    mood = audio_state.analysis.get('mood', {})
                    for mood_name, value in sorted(mood.items()):
                        # Color-code mood values
                        if value > 0.7:
                            imgui.text_colored(f"{mood_name.capitalize()}: {value:.2f}", 0.3, 1.0, 0.3)
                        elif value > 0.3:
                            imgui.text_colored(f"{mood_name.capitalize()}: {value:.2f}", 1.0, 1.0, 0.3)
                        else:
                            imgui.text(f"{mood_name.capitalize()}: {value:.2f}")

                    # Danceability
                    imgui.spacing()
                    imgui.text_colored("Danceability:", 1.0, 1.0, 0.5)
                    dance_val = audio_state.analysis['danceability']
                    if dance_val > 0.7:
                        imgui.text_colored(f"{dance_val:.2f} (High)", 0.3, 1.0, 0.3)
                    elif dance_val > 0.4:
                        imgui.text_colored(f"{dance_val:.2f} (Medium)", 1.0, 1.0, 0.3)
                    else:
                        imgui.text_colored(f"{dance_val:.2f} (Low)", 1.0, 0.5, 0.3)

                    # Structure/Segments
                    imgui.spacing()
                    imgui.text_colored("Song Structure:", 1.0, 1.0, 0.5)
                    segments = audio_state.analysis.get('segments', [])
                    imgui.text(f"{len(segments)} sections detected:")
                    for seg in segments:
                        duration = seg['end'] - seg['start']
                        imgui.text(f"  {seg['start']:.0f}s-{seg['end']:.0f}s: {seg['label']} ({duration:.0f}s)")
                # End of collapsible header content
                imgui.spacing()
                imgui.separator()

                # LLM provider selection
                imgui.spacing()
                imgui.text("LLM Provider:")
                if imgui.radio_button("Anthropic Claude", llm_provider == "anthropic"):
                    llm_provider = "anthropic"
                imgui.same_line()
                if imgui.radio_button("Ollama (local)", llm_provider == "ollama"):
                    llm_provider = "ollama"

                # Generate recommendation
                imgui.spacing()
                if is_generating:
                    imgui.text_colored("Generating...", 1.0, 1.0, 0.3)
                else:
                    if imgui.button("Generate Choreography", 180, 35):
                        # Run generation in background thread
                        threading.Thread(target=generate_choreography, daemon=True).start()

            # Display choreography recommendation
            if choreography_recommendation:
                imgui.spacing()
                imgui.separator()
                imgui.text_colored("Recommended Choreography:", 0.3, 1.0, 0.3)

                choreo_moves = choreography_recommendation.get('sequence', choreography_recommendation.get('choreography', []))
                total_duration = choreography_recommendation.get('total_duration_filled', choreography_recommendation.get('total_duration', 0))
                bpm = choreography_recommendation.get('bpm', 0)

                imgui.text(f"Total moves: {len(choreo_moves)}")
                imgui.text(f"Coverage: {total_duration:.1f}s")
                if bpm:
                    imgui.text(f"BPM: {bpm:.1f}")

                # Move list (ALL moves, scrollable)
                imgui.spacing()
                imgui.text_colored("Moves:", 1.0, 1.0, 0.5)

                # Scrollable region for all moves
                imgui.begin_child("move_list", 0, 200, border=True)

                for i, move in enumerate(choreo_moves):
                    # Handle both old and new formats
                    move_name = move.get('move') or move.get('move_name', 'unknown')

                    # Determine type correctly
                    if move_name == 'manual':
                        move_type = 'manual'
                    elif move_name == 'idle':
                        move_type = 'idle'
                    else:
                        move_type = 'dance' if move_name in dances else 'emotion'

                    cycles = move.get('cycles', 1)
                    duration = move.get('duration', 0)

                    # Format display
                    if move_name == 'idle':
                        display_str = f"{i}. idle {duration:.1f}s"
                    elif move_name == 'manual':
                        body_yaw = move.get('body_yaw', 0)
                        display_str = f"{i}. manual (yaw={body_yaw:.0f}°, {duration:.1f}s)"
                    else:
                        display_str = f"{i}. {cycles}x {move_name} ({move_type})"

                    # Display move
                    if imgui.selectable(display_str, selected_move_index == i)[0]:
                        selected_move_index = i

                    # Show details on hover
                    if imgui.is_item_hovered():
                        imgui.begin_tooltip()
                        imgui.text(f"Type: {move_type}")
                        if move_name not in ['idle', 'manual']:
                            imgui.text(f"Cycles: {cycles}")
                        imgui.text(f"Reasoning: {move.get('reasoning', 'N/A')}")
                        imgui.end_tooltip()

                imgui.end_child()

                # Feedback for RL training
                imgui.spacing()
                imgui.separator()
                imgui.text_colored("Feedback (for RL improvement):", 1.0, 0.8, 0.3)

                # Feedback text input
                changed, user_feedback = imgui.input_text_multiline(
                    "##feedback",
                    user_feedback,
                    500,  # max length
                    -1,   # width (full)
                    60    # height (3 lines)
                )

                imgui.same_line()
                if imgui.button("Regenerate\nwith Feedback", 120, 60):
                    if user_feedback.strip():
                        threading.Thread(
                            target=lambda: generate_choreography_with_feedback(user_feedback),
                            daemon=True
                        ).start()
                    else:
                        status_message = "✗ Enter feedback first"
                        status_message_time = time.time()

                # Playback controls
                imgui.spacing()
                imgui.separator()
                imgui.text_colored("Playback:", 1.0, 1.0, 0.5)

                if is_playing_audio:
                    imgui.text_colored("▶ Playing...", 0.3, 1.0, 0.3)
                    if current_choreo_move_info:
                        imgui.text(f"Current: {current_choreo_move_info}")
                    if imgui.button("■ Stop Playback", 200, 40):
                        stop_audio_playback()
                else:
                    if imgui.button("▶ Play Choreography with Audio", 280, 40):
                        play_choreography_with_audio()

                # Export button
                imgui.spacing()
                if imgui.button("Export Final Choreography", 200, 35):
                    export_final_choreography()

        imgui.end()  # End Choreography Builder window

        # --- MuJoCo Rendering ---
        viewport_width, viewport_height = glfw.get_framebuffer_size(window)
        viewport = mujoco.MjrRect(0, 0, viewport_width, viewport_height)

        # Clear buffers
        glClearColor(0.2, 0.3, 0.4, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        # Render MuJoCo scene
        mujoco.mjv_updateScene(model, data, opt, None, cam, mujoco.mjtCatBit.mjCAT_ALL, scn)
        mujoco.mjr_render(viewport, scn, ctx)

        # --- ImGui Rendering ---
        imgui.render()

        # Set up 2D rendering for ImGui (on top of 3D scene)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
        glDisable(GL_LIGHTING)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_SCISSOR_TEST)
        glEnable(GL_TEXTURE_2D)

        # Set white color (important for fixed pipeline)
        glColor4f(1.0, 1.0, 1.0, 1.0)

        # Viewport for ImGui
        glViewport(0, 0, viewport_width, viewport_height)

        # Set up orthographic projection for 2D
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0.0, viewport_width, viewport_height, 0.0, -1.0, 1.0)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        # Render ImGui
        impl.render(imgui.get_draw_data())

        # Restore state
        glDisable(GL_SCISSOR_TEST)
        glEnable(GL_DEPTH_TEST)

        glfw.swap_buffers(window)
        glfw.poll_events()

        time.sleep(max(0, model.opt.timestep - (time.time() - loop_start)))

    # --- Shutdown ---
    impl.shutdown()
    glfw.terminate()

if __name__ == "__main__":
    main()