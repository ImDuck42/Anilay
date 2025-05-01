#!/usr/bin/env python3

import os
import sys
import numpy as np
import pyaudio
import threading
import time
import logging
import configparser
from pathlib import Path
import argparse
import tkinter as tk
from tkinter import Canvas
from PIL import Image, ImageTk, ImageSequence
import ctypes
import win32gui
import win32con
import win32api

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MicReactiveDisplay')

# Default configuration
DEFAULT_CONFIG = {
    "Audio": {
        "rate": "44100", # Sample rate
        "chunk": "1024", # Buffer size
        "channels": "1", # 1 = Mono 2 = Stereo
        "device_index": "-1", # -1 for default device
    },
    "Thresholds": {
        "default": "5",     # Default threshold
        "talking": "20",    # Talking threshold
        "screaming": "2000" # Screaming threshold
    },
    "Display": {
        "default": {
            "image": ".\\sources\\normal.png",
            "max_width": "100",
            "max_height": "100",
            "x_offset": "50",
            "y_offset": "50"
        },
        "talking": {
            "image": ".\\sources\\talking.png",
            "max_width": "100",
            "max_height": "100",
            "x_offset": "50",
            "y_offset": "50"
        },
        "screaming": {
            "image": ".\\sources\\screaming.png",
            "max_width": "100",
            "max_height": "100",
            "x_offset": "50",
            "y_offset": "50"
        }
    }
}

class Config:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "anilay.ini")
        
        # Load or create config
        if os.path.exists(self.config_file):
            try:
                self.config.read(self.config_file)
                logger.info(f"Loaded configuration from {self.config_file}")
            except Exception as e:
                logger.error(f"Error reading config: {e}")
                self._set_defaults()
        else:
            self._set_defaults()
            self._save_config()
    
    def _set_defaults(self):
        """Set default configuration values"""
        for section, values in DEFAULT_CONFIG.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
            for key, value in values.items():
                if isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        self.config.set(section, f"{key}.{sub_key}", str(sub_value))
                else:
                    self.config.set(section, key, value)
    
    def _save_config(self):
        """Save configuration to file"""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, 'w') as f:
            self.config.write(f)
        logger.info(f"Configuration saved to {self.config_file}")
    
    def get_audio_config(self):
        """Get audio configuration values"""
        section = self.config['Audio']
        return {
            'rate': int(section.get('rate')),
            'chunk': int(section.get('chunk')),
            'channels': int(section.get('channels')),
            'device_index': int(section.get('device_index', '-1'))  # Default to -1 if missing
        }
    
    def get_thresholds(self):
        """Get threshold configuration values"""
        section = self.config['Thresholds']
        return {key: int(value) for key, value in section.items()}
    
    def get_display_states(self):
        """Get display states from configuration"""
        section = self.config['Display']
        states = {}
        
        # Parse display states from config
        for key in section:
            parts = key.split('.')
            if len(parts) == 2:
                state_name, property_name = parts
                if state_name not in states:
                    states[state_name] = {}
                states[state_name][property_name] = section[key]
        
        return states
    
    def get_display_config(self):
        """Get display configuration values"""
        display_states = self.get_display_states()
        default_state = display_states.get('default', {})
        
        return {
            'states': display_states,
            'max_width': int(default_state.get('max_width', 100)),
            'max_height': int(default_state.get('max_height', 100)),
            'x_offset': int(default_state.get('x_offset', 50)),
            'y_offset': int(default_state.get('y_offset', 50))
        }

class AudioProcessor:
    """Handles audio processing and detection"""
    
    def __init__(self, audio_config, thresholds_config, callback):
        self.audio_config = audio_config
        self.thresholds = thresholds_config
        self.callback = callback
        self.running = False
        self.stream = None
        self.p = None
        self.thread = None
        self.last_active_time = time.time()
        self.is_active = False
        self.current_state = None
        
        # Create sorted thresholds for processing (largest to smallest)
        self.sorted_thresholds = sorted(
            self.thresholds.items(), 
            key=lambda x: x[1], 
            reverse=True
        )
    
    def start(self):
        """Start audio processing thread"""
        if self.thread and self.thread.is_alive():
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._audio_detection_thread)
        self.thread.daemon = True
        self.thread.start()
        logger.info("Audio detection thread started")
    
    def stop(self):
        """Stop audio processing thread and clean up resources"""
        self.running = False
        
        # Close and clean up resources
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
        
        if self.p:
            try:
                self.p.terminate()
                self.p = None
            except Exception as e:
                logger.error(f"Error terminating PyAudio: {e}")
        
        # Wait for thread to finish
        if self.thread and self.thread.is_alive():
            self.thread.join(1.0)
            self.thread = None
            
        logger.info("Audio processor stopped")
    
    def _audio_detection_thread(self):
        """Thread to detect microphone input"""
        try:
            self.p = pyaudio.PyAudio()
            
            # Open audio stream
            device_index = self.audio_config['device_index']
            if device_index < 0:
                device_index = None  # Use default device
                
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=self.audio_config['channels'],
                rate=self.audio_config['rate'],
                input=True,
                frames_per_buffer=self.audio_config['chunk'],
                input_device_index=device_index
            )
            
            # Buffer for better performance
            chunk_size = self.audio_config['chunk']
            
            while self.running:
                try:
                    # Read audio data
                    data = np.frombuffer(
                        self.stream.read(chunk_size, exception_on_overflow=False), 
                        dtype=np.int16
                    )
                    
                    # Calculate volume (root mean square for better accuracy)
                    volume = np.sqrt(np.mean(np.square(data.astype(np.float32))))
                    
                    # Check thresholds from highest to lowest
                    new_state = None
                    for state, threshold in self.sorted_thresholds:
                        if volume > threshold:
                            new_state = state
                            self.last_active_time = time.time()
                            self.is_active = True
                            break
                    
                    # If no threshold met, check silence timeout
                    if new_state is None and self.is_active:
                        self.is_active = False
                    
                    # Only update if state changed
                    if new_state != self.current_state:
                        self.current_state = new_state
                        self.callback(new_state)
                        
                except IOError:
                    # Less verbose error handling for IO errors (usually just buffer overruns)
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error in audio detection: {e}")
                    time.sleep(0.5)
                    
        except Exception as e:
            logger.error(f"Fatal error in audio thread: {e}")
        finally:
            self.stop()  # Clean up resources

class TransparentWindow:
    def __init__(self, dragable=False):
        # Load configuration
        self.config = Config()
        self.display_config = self.config.get_display_config()
        self.display_states = self.display_config['states']
        
        # Create the main window
        self.root = tk.Tk()
        self.root.title("Anilay")
        self.root.overrideredirect(True)  # No window decorations
        self.root.attributes("-topmost", True)  # Keep on top
        self.root.attributes("-transparentcolor", "black")  # Transparent background (Windows specific)
        self.root.configure(bg="black")
        
        # Store the dragable state
        self.dragable = dragable
        
        # Create canvas for image display
        self.canvas = Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Initialize state variables
        self.current_state = None
        self.current_image_path = None
        self.animation_frame = None
        self.animation_id = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        
        # Set up event bindings
        if self.dragable:
            self.canvas.bind("<ButtonPress-1>", self.on_press)
            self.canvas.bind("<B1-Motion>", self.on_drag)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Initialize audio processor
        audio_config = self.config.get_audio_config()
        thresholds_config = self.config.get_thresholds()
        self.audio_processor = AudioProcessor(audio_config, thresholds_config, self.set_state_from_thread)
        
        # Setup for click-through (Windows specific)
        if not self.dragable:
            self.make_click_through()
        
        # Position window
        self.position_window()
        
        # Use default state for initial image
        default_state = next(iter(self.display_states)) if self.display_states else None
        if default_state and 'image' in self.display_states[default_state]:
            self.load_image(self.display_states[default_state]['image'])
    
    def make_click_through(self):
        """Make the window click-through (Windows specific)"""
        try:
            # This needs to be called after the window is shown
            hwnd = ctypes.windll.user32.FindWindowW(None, self.root.title())
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            style = style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
        except Exception as e:
            logger.error(f"Failed to make window click-through: {e}")
    
    def on_press(self, event):
        """Handle mouse button press for dragging"""
        self.drag_start_x = event.x
        self.drag_start_y = event.y
    
    def on_drag(self, event):
        """Handle mouse dragging to move window"""
        x = self.root.winfo_x() + (event.x - self.drag_start_x)
        y = self.root.winfo_y() + (event.y - self.drag_start_y)
        self.root.geometry(f"+{x}+{y}")
        
        # Update position offsets
        self.display_config['x_offset'] = x
        self.display_config['y_offset'] = y
    
    def position_window(self):
        """Position the window with configured offsets"""
        x = self.display_config['x_offset']
        y = self.display_config['y_offset']
        self.root.geometry(f"+{x}+{y}")
    
    def load_image(self, image_path):
        """Load an image or animation with error handling"""
        if self.current_image_path == image_path:
            return  # Don't reload if it's the same image
            
        self.current_image_path = image_path
        
        try:
            path = Path(image_path)
            if not path.exists():
                logger.error(f"Image file not found: {image_path}")
                return
                
            # Stop any running animation
            self._stop_animation()
            
            # Clear previous image
            self.canvas.delete("all")
            
            # Load the image with PIL
            image = Image.open(str(path))
            
            if path.suffix.lower() == '.gif' and 'duration' in image.info:
                # Handle animated GIFs
                self.frames = []
                self.frame_durations = []
                
                for frame in ImageSequence.Iterator(image):
                    # Scale the frame
                    frame = self._scale_image(frame)
                    # Convert to PhotoImage
                    photo = ImageTk.PhotoImage(frame)
                    self.frames.append(photo)
                    # Get frame duration
                    duration = frame.info.get('duration', 100)
                    self.frame_durations.append(duration)
                
                # Start the animation
                if self.frames:
                    self.current_frame = 0
                    self.animation_image = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.frames[0])
                    self.root.update()
                    self.root.geometry(f"{self.frames[0].width()}x{self.frames[0].height()}")
                    self._animate_gif()
            else:
                # Handle static images
                image = self._scale_image(image)
                self.photo = ImageTk.PhotoImage(image)
                self.animation_image = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
                self.root.update()
                self.root.geometry(f"{self.photo.width()}x{self.photo.height()}")
            
            # Reposition after resize
            self.position_window()
            
        except Exception as e:
            logger.error(f"Error loading image {image_path}: {e}")
    
    def _scale_image(self, image):
        """Scale the image to fit within max dimensions while maintaining aspect ratio"""
        width, height = image.size
        
        max_width = self.display_config['max_width']
        max_height = self.display_config['max_height']

        if width > max_width or height > max_height:
            scale_factor = min(max_width / width, max_height / height)
            new_width = int(width * scale_factor)
            new_height = int(height * scale_factor)
            return image.resize((new_width, new_height), Image.LANCZOS)
        
        return image
    
    def _animate_gif(self):
        """Animate a GIF image"""
        if not hasattr(self, 'frames') or not self.frames:
            return
            
        # Cancel any existing animation
        if self.animation_id:
            self.root.after_cancel(self.animation_id)
            
        # Update current frame
        self.current_frame = (self.current_frame + 1) % len(self.frames)
        self.canvas.itemconfig(self.animation_image, image=self.frames[self.current_frame])
        
        # Schedule next frame
        duration = self.frame_durations[self.current_frame]
        self.animation_id = self.root.after(duration, self._animate_gif)
    
    def _stop_animation(self):
        """Stop any running animation"""
        if hasattr(self, 'animation_id') and self.animation_id:
            self.root.after_cancel(self.animation_id)
            self.animation_id = None
    
    def set_state_from_thread(self, state):
        """Set the state from a non-GUI thread"""
        self.root.after(0, lambda: self.set_state(state))
    
    def set_state(self, state):
        """Set the current state and update the image"""
        if state == self.current_state:
            return
            
        self.current_state = state
        self.update_image_state()
    
    def update_image_state(self):
        """Update the displayed image based on current state"""
        # If state is None, use the lowest threshold state (typically "default")
        if self.current_state is None:
            # Get thresholds with proper type conversion
            thresholds = self.config.get_thresholds()
            default_state = min(self.display_states.keys(), 
                               key=lambda s: thresholds.get(s, 0) if s in thresholds else float('inf'))
            if default_state in self.display_states and 'image' in self.display_states[default_state]:
                self.load_image(self.display_states[default_state]['image'])
        else:
            # Use the state-specific image if it exists
            if self.current_state in self.display_states and 'image' in self.display_states[self.current_state]:
                self.load_image(self.display_states[self.current_state]['image'])
    
    def start(self):
        """Start the window and audio processor"""
        self.audio_processor.start()
        
        # Make the window click-through if needed
        if not self.dragable:
            # Needs to be delayed slightly to work properly
            self.root.after(100, self.make_click_through)
            
        self.root.mainloop()
    
    def on_close(self):
        """Handle window close"""
        self.cleanup()
        self.root.destroy()
    
    def cleanup(self):
        """Clean up resources before exit"""
        logger.info("Cleaning up resources...")
        
        # Stop audio processing
        if hasattr(self, 'audio_processor'):
            self.audio_processor.stop()
        
        # Stop animations
        self._stop_animation()

def list_audio_devices():
    """List available audio input devices"""
    import pyaudio
    p = pyaudio.PyAudio()
    print("\nAvailable audio input devices:")
    print("-" * 50)
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if dev.get('maxInputChannels') > 0:
            print(f"Index {i}: {dev.get('name')}")
            print(f"  Channels: {dev.get('maxInputChannels')}")
            print(f"  Sample Rate: {dev.get('defaultSampleRate')}\n")
    p.terminate()

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Anilay for Windows")
    parser.add_argument("--dragable", action="store_true", help="Enable dragable mode for the window")
    parser.add_argument("--devices", action="store_true", help="List available audio input devices")
    args = parser.parse_args()
    
    # Handle --devices parameter
    if args.devices:
        list_audio_devices()
        return
    
    # Initialize and run
    win = TransparentWindow(dragable=args.dragable)
    
    try:
        win.start()
    except KeyboardInterrupt:
        win.on_close()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        win.cleanup()
        sys.exit(1)

if __name__ == "__main__":
    main()