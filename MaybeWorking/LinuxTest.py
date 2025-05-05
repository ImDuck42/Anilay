#!/usr/bin/env python3
"""
Anilay - Visual microphone activity indicator for Linux
A lightweight application that shows animated indicators when microphone activity is detected.
"""

# Imports
import os, sys, time, logging, argparse, threading,configparser
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, Callable, Any

import numpy as np
import pyaudio
import gi

# GTK setup
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('Anilay')

# Default configuration
DEFAULT_CONFIG = {
    "Audio": {
        "rate": "44100", 
        "chunk": "1024", 
        "channels": "1", 
        "silence_timeout": "0.1"
    },
    "Thresholds": {
        "default": "15", 
        "talking": "50", 
        "screaming": "2000"
    },
    "Display": {
        "default": {
            "image": "normal.png", 
            "max_width": "100", 
            "max_height": "100", 
            "x_offset": "50", 
            "y_offset": "50"
        },
        "talking": {
            "image": "talking.png", 
            "max_width": "100", 
            "max_height": "100", 
            "x_offset": "0", 
            "y_offset": "-10"
        },
        "screaming": {
            "image": "screaming.png", 
            "max_width": "100", 
            "max_height": "100", 
            "x_offset": "0", 
            "y_offset": "-25"
        }
    }
}


@contextmanager
def audio_stream(p_audio, **kwargs):
    """Context manager for PyAudio stream to ensure proper resource cleanup."""
    stream = p_audio.open(**kwargs)
    try:
        yield stream
    finally:
        if stream:
            stream.stop_stream()
            stream.close()


class ConfigManager:
    """Manages configuration loading, validation and access."""
    
    def __init__(self, skin_path: str):
        self.config = configparser.ConfigParser()
        self.skin_path = skin_path
        self.config_file = os.path.join(skin_path, "config.ini")
        
        self._load_or_create_config()
    
    def _load_or_create_config(self) -> None:
        """Load existing config or create with defaults if not found."""
        try:
            if os.path.exists(self.config_file):
                self.config.read(self.config_file)
                logger.info(f"Loaded configuration from {self.config_file}")
                self._validate_config()
            else:
                logger.warning(f"Config file not found. Creating default at {self.config_file}")
                self._set_defaults()
                self._save_config()
        except Exception as e:
            logger.error(f"Error with config file: {e}. Using defaults.")
            self._set_defaults()
    
    def _validate_config(self) -> None:
        """Ensure all required config sections and options exist."""
        needs_update = False
        
        # Validate all sections
        for section, values in DEFAULT_CONFIG.items():
            if section == "Display":
                for mode, mode_values in values.items():
                    section_name = f"Display_{mode}"
                    if not self.config.has_section(section_name):
                        self.config.add_section(section_name)
                        needs_update = True
                    for key, value in mode_values.items():
                        if not self.config.has_option(section_name, key):
                            self.config.set(section_name, key, value)
                            needs_update = True
            else:
                if not self.config.has_section(section):
                    self.config.add_section(section)
                    needs_update = True
                for key, value in values.items():
                    if not self.config.has_option(section, key):
                        self.config.set(section, key, value)
                        needs_update = True
        
        if needs_update:
            self._save_config()
    
    def _set_defaults(self) -> None:
        """Set all configuration options to their default values."""
        for section, values in DEFAULT_CONFIG.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
            
            if section == "Display":
                for mode, mode_values in values.items():
                    section_name = f"Display_{mode}"
                    if not self.config.has_section(section_name):
                        self.config.add_section(section_name)
                    for key, value in mode_values.items():
                        self.config.set(section_name, key, value)
            else:
                for key, value in values.items():
                    self.config.set(section, key, value)
    
    def _save_config(self) -> None:
        """Save current configuration to file."""
        try:
            with open(self.config_file, 'w') as configfile:
                self.config.write(configfile)
            logger.info(f"Configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
    
    def get_audio_config(self) -> Dict[str, Any]:
        """Get audio processing configuration parameters."""
        section = self.config['Audio']
        return {
            'rate': int(section.get('rate')),
            'chunk': int(section.get('chunk')),
            'channels': int(section.get('channels')),
            'silence_timeout': float(section.get('silence_timeout'))
        }
    
    def get_thresholds(self) -> Dict[str, int]:
        """Get audio volume thresholds for different states."""
        section = self.config['Thresholds']
        return {
            'default': int(section.get('default')),
            'talking': int(section.get('talking')),
            'screaming': int(section.get('screaming'))
        }
    
    def get_display_config(self, mode: str = "default") -> Dict[str, Any]:
        """Get display configuration for the specified mode."""
        section_name = f"Display_{mode}"
        
        if not self.config.has_section(section_name):
            logger.warning(f"Display mode {mode} not found, using default")
            section_name = "Display_default"
        
        section = self.config[section_name]
        
        # Resolve paths relative to skin directory
        image_path = section.get('image')
        if not os.path.isabs(image_path):
            image_path = os.path.join(self.skin_path, image_path)
        
        return {
            'image': image_path,
            'max_width': int(section.get('max_width', 100)),
            'max_height': int(section.get('max_height', 100)),
            'x_offset': int(section.get('x_offset', 50)),
            'y_offset': int(section.get('y_offset', 50))
        }
    
    def get_all_display_modes(self) -> list:
        """Get list of all configured display modes."""
        return [section[8:] for section in self.config.sections() 
                if section.startswith("Display_")]


class AudioProcessor:
    """Processes microphone input to detect sound levels."""
    
    def __init__(self, config: ConfigManager, on_mode_change: Callable[[str], None]):
        self.audio_config = config.get_audio_config()
        self.thresholds = config.get_thresholds()
        self.on_mode_change = on_mode_change
        self.running = False
        self.audio = None
        self.thread = None
        self.last_active_time = 0
        self.current_mode = "default"
    
    def start(self) -> None:
        """Start the audio processing thread."""
        if self.thread and self.thread.is_alive():
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._audio_detection_loop)
        self.thread.daemon = True
        self.thread.start()
        logger.info("Audio detection thread started")
    
    def stop(self) -> None:
        """Stop the audio processing thread and clean up resources."""
        if not self.running:
            return
            
        self.running = False
        
        if self.thread and self.thread.is_alive():
            self.thread.join(1.0)
            
        logger.info("Audio processor stopped")
    
    def _audio_detection_loop(self) -> None:
        """Continuously monitor audio input and detect volume changes."""
        try:
            self.audio = pyaudio.PyAudio()
            
            stream_params = {
                'format': pyaudio.paInt16,
                'channels': self.audio_config['channels'],
                'rate': self.audio_config['rate'],
                'input': True,
                'frames_per_buffer': self.audio_config['chunk']
            }
            
            with audio_stream(self.audio, **stream_params) as stream:
                chunk_size = self.audio_config['chunk']
                silence_timeout = self.audio_config['silence_timeout']
                
                while self.running:
                    try:
                        # Read audio data
                        data = np.frombuffer(
                            stream.read(chunk_size, exception_on_overflow=False), 
                            dtype=np.int16
                        )
                        
                        # Calculate volume (RMS)
                        volume = np.sqrt(np.mean(np.square(data.astype(np.float32))))
                        
                        # Determine mode based on volume
                        new_mode = self._get_mode_for_volume(volume)
                        self._update_mode_if_needed(new_mode, silence_timeout)
                            
                    except IOError as e:
                        logger.warning(f"Audio stream IOError: {e}")
                        time.sleep(0.1)
                    except Exception as e:
                        logger.error(f"Error in audio detection: {e}")
                        time.sleep(0.5)
                        
        except Exception as e:
            logger.error(f"Fatal error in audio thread: {e}")
        finally:
            self._cleanup_audio()
    
    def _get_mode_for_volume(self, volume: float) -> str:
        """Determine the appropriate mode based on volume level."""
        if volume > self.thresholds['screaming']:
            return "screaming"
        elif volume > self.thresholds['talking']:
            return "talking"
        return "default"
    
    def _update_mode_if_needed(self, new_mode: str, silence_timeout: float) -> None:
        """Update the current mode if needed based on audio state."""
        if new_mode != "default":
            self.last_active_time = time.time()
            if self.current_mode != new_mode:
                self.current_mode = new_mode
                GLib.idle_add(self.on_mode_change, new_mode)
        elif (time.time() - self.last_active_time) > silence_timeout and self.current_mode != "default":
            self.current_mode = "default"
            GLib.idle_add(self.on_mode_change, "default")
    
    def _cleanup_audio(self) -> None:
        """Clean up PyAudio resources."""
        if self.audio:
            try:
                self.audio.terminate()
            except:
                pass
            self.audio = None


class AnilayWindow(Gtk.Window):
    """Main transparent window that displays the current state image."""
    
    def __init__(self, config: ConfigManager):
        Gtk.Window.__init__(self, title="Anilay")
        
        # Store configuration and state
        self.config = config
        self.current_mode = "default"
        self.display_config = self.config.get_display_config(self.current_mode)
        self.running = False
        self.frame_timeout_id = None
        self.current_image_path = None
        self.initial_position_set = False
        self.base_position = (0, 0)
        self.current_position = (0, 0)
        
        self._setup_window_properties()
        self._connect_signal_handlers()
        
        # Create image container
        self.image = Gtk.Image()
        self.add(self.image)
        
        # Initialize audio processor
        self.audio_processor = AudioProcessor(self.config, self.set_mode)
        
        # Load the default image
        self.load_image(self.display_config['image'])
    
    def _setup_window_properties(self) -> None:
        """Configure window properties for always-on-top transparent window."""
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_resizable(False)
        self.stick()  # Make visible across all workspaces
        
        # Make window transparent
        self.set_app_paintable(True)
        self._setup_visual()
    
    def _setup_visual(self, widget=None, previous_screen=None) -> None:
        """Set up the visual for transparency support."""
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
    
    def _connect_signal_handlers(self) -> None:
        """Connect GTK event signals to handler methods."""
        self.connect("draw", self._on_draw)
        self.connect("destroy", self._on_destroy)
        self.connect("screen-changed", self._setup_visual)
        self.connect("button-press-event", self._on_button_press)

    def _on_draw(self, widget, cr) -> bool:
        """Make the window background transparent."""
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(1)  # Clear operator
        cr.paint()
        return False
    
    def _on_button_press(self, widget, event) -> bool:
        """Handle mouse button clicks for window dragging."""
        if event.button == 1:  # Left mouse button
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            # Update our stored position after the move completes
            GLib.timeout_add(100, self._update_position_after_drag)
        return True
        
    def _update_position_after_drag(self) -> bool:
        """Store the new window position after user drag is completed."""
        if self.get_window() and self.get_window().is_visible():
            self.base_position = self.get_position()
            self.current_position = self.base_position
            logger.debug(f"Updated base position to {self.base_position} after drag")
        return False  # Don't repeat

    def load_image(self, image_path: str) -> None:
        """Load and display an image or animation from the given path."""
        if self.current_image_path == image_path:
            return  # Don't reload if it's the same image
            
        self.current_image_path = image_path
        
        try:
            path = Path(image_path)
            if not path.exists():
                logger.error(f"Image file not found: {image_path}")
                return
                
            if path.suffix.lower() == '.gif':
                self._load_animation(path)
            else:
                self._load_static_image(path)
            
            # Position the window after loading the image
            if not self.initial_position_set:
                self.position_window(initial_placement=True)
                self.initial_position_set = True
            else:
                # Apply offsets for the current mode
                self.apply_offset_for_mode()
            
        except GLib.Error as e:
            logger.error(f"Error loading image {image_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading image {image_path}: {e}")
    
    def _load_animation(self, path: Path) -> None:
        """Load an animated GIF file."""
        animation = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
        self.image.set_from_animation(animation)
        
        if not animation.is_static_image():
            self._start_animation_loop(animation.get_iter(None))
        else:
            # Static GIF
            pixbuf = animation.get_static_image()
            pixbuf = self._scale_pixbuf(pixbuf)
            self.image.set_from_pixbuf(pixbuf)
            self.resize(pixbuf.get_width(), pixbuf.get_height())
    
    def _load_static_image(self, path: Path) -> None:
        """Load a static image file."""
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
        pixbuf = self._scale_pixbuf(pixbuf)
        self.image.set_from_pixbuf(pixbuf)
        self.resize(pixbuf.get_width(), pixbuf.get_height())

    def _scale_pixbuf(self, pixbuf) -> GdkPixbuf.Pixbuf:
        """Scale the pixbuf to fit within max dimensions while maintaining aspect ratio."""
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        
        max_width = self.display_config['max_width']
        max_height = self.display_config['max_height']

        # Calculate scale factor to fit within max dimensions
        scale_factor = min(max_width / width, max_height / height)
        if scale_factor >= 1:  # Don't upscale
            return pixbuf
            
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        return pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)

    def _start_animation_loop(self, iter) -> None:
        """Start the animation loop for GIFs."""
        if self.frame_timeout_id:
            GLib.source_remove(self.frame_timeout_id)
            self.frame_timeout_id = None
        
        def update_animation():
            if not self.running:
                return False
                
            pixbuf = iter.get_pixbuf()
            scaled_pixbuf = self._scale_pixbuf(pixbuf)
            self.image.set_from_pixbuf(scaled_pixbuf)
            
            delay = max(iter.get_delay_time(), 10)  # Ensure minimum delay
            iter.advance()
            
            # Schedule next frame
            self.frame_timeout_id = GLib.timeout_add(delay, update_animation)
            return False
        
        # Start first frame
        update_animation()

    def position_window(self, initial_placement: bool = False) -> None:
        """Position the window based on display configuration."""
        display = Gdk.Display.get_default()
        
        # Get the monitor geometry
        if self.get_window() and self.get_window().is_visible():
            monitor = display.get_monitor_at_window(self.get_window())
        else:
            monitor = display.get_monitor(0)
        
        geometry = monitor.get_geometry()
        
        if initial_placement or not self.get_window() or not self.get_window().is_visible():
            # Initial placement based on monitor geometry
            x = geometry.x + geometry.width - self.display_config['max_width'] - self.display_config['x_offset']
            y = geometry.y + geometry.height - self.display_config['max_height'] - self.display_config['y_offset']
            
            # Store this as our base position
            self.base_position = (x, y)
            self.current_position = (x, y)
            
            self.move(x, y)
            logger.debug(f"Set initial position to {x}, {y}")
        else:
            # Apply offsets for the current mode
            self.apply_offset_for_mode()

    def apply_offset_for_mode(self) -> None:
        """Apply the x and y offsets for the current mode."""
        x_offset = self.display_config['x_offset']
        y_offset = self.display_config['y_offset']
        
        # Calculate new position with offsets
        if self.current_mode == "default":
            # Return to base position for default mode
            new_x, new_y = self.base_position
        else:
            # Apply offsets from base position
            base_x, base_y = self.base_position
            new_x = base_x + x_offset
            new_y = base_y + y_offset
        
        # Update current position and move window
        self.current_position = (new_x, new_y)
        self.move(new_x, new_y)
        logger.debug(f"Applied offset for mode {self.current_mode}, position now {new_x}, {new_y}")

    def set_mode(self, mode: str) -> None:
        """Set the display mode and update the image."""
        if mode == self.current_mode:
            return
            
        # Update mode and configuration
        old_mode = self.current_mode
        self.current_mode = mode
        self.display_config = self.config.get_display_config(mode)
        
        # Stop animation if playing
        if self.frame_timeout_id:
            GLib.source_remove(self.frame_timeout_id)
            self.frame_timeout_id = None
        
        # Update the image and apply offsets
        self.load_image(self.display_config['image'])
        
        logger.debug(f"Mode changed from {old_mode} to {mode}")
    
    def _on_destroy(self, widget) -> None:
        """Handle window destruction."""
        self.cleanup()
        Gtk.main_quit()

    def start(self) -> None:
        """Start the window and audio processor."""
        self.show_all()
        self.running = True
        self.audio_processor.start()

    def cleanup(self) -> None:
        """Cleanup resources on exit."""
        logger.info("Cleaning up resources...")
        self.running = False
        
        # Stop audio processing
        if hasattr(self, 'audio_processor'):
            self.audio_processor.stop()
        
        # Remove any pending animation timeouts
        if self.frame_timeout_id:
            GLib.source_remove(self.frame_timeout_id)
            self.frame_timeout_id = None


def setup_signal_handlers(window: AnilayWindow) -> None:
    """Setup signal handlers for proper termination."""
    if hasattr(GLib, 'unix_signal_add'):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, lambda: (window.destroy(), True))  # SIGINT
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, lambda: (window.destroy(), True))  # SIGTERM


def validate_skin_path(skin_path: str) -> str:
    """Validate that the skin path exists and has the correct extension."""
    abs_path = os.path.abspath(skin_path)
    if not os.path.isdir(abs_path):
        logger.error(f"Skin folder not found: {abs_path}")
        sys.exit(1)
    
    if not abs_path.endswith('.al'):
        logger.error(f"Skin folder must end with '.al': {abs_path}")
        sys.exit(1)
    
    return abs_path


def main() -> None:
    """Main function to parse arguments and start the application."""
    # Set application name for proper window management
    GLib.set_application_name("Anilay")
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Anilay - Shows a visual indicator when microphone detects sound"
    )
    parser.add_argument('--skin', required=True, help="Path to the skin folder")
    args = parser.parse_args()
    
    # Validate skin path
    skin_path = validate_skin_path(args.skin)
    logger.info(f"Using skin: {skin_path}")
    
    # Initialize configuration
    config = ConfigManager(skin_path)
    
    # Initialize and run
    window = AnilayWindow(config)
    
    try:
        setup_signal_handlers(window)
        window.start()
        Gtk.main()
    except KeyboardInterrupt:
        window.destroy()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()