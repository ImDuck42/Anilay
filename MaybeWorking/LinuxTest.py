#!/usr/bin/env python3

import os, gi, sys, time, pyaudio, logging, argparse, threading, numpy as np, configparser
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, Any, Callable, Optional, Union

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('Anilay')

# Setup GTK requirements
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

# Default configuration dictionary
DEFAULT_CONFIG = {
    "Audio": {"rate": "44100", "chunk": "1024", "channels": "1", "threshold": "50", "silence_timeout": "0.5"},
    "Thresholds": {"default": "5", "talking": "20", "screaming": "2000"},
    "Display": {
        "default": {"image": "normal.png", "max_width": "100", "max_height": "100", "x_offset": "50", "y_offset": "50"},
        "talking": {"image": "talking.png", "max_width": "100", "max_height": "100", "x_offset": "0", "y_offset": "-10"},
        "screaming": {"image": "screaming.png", "max_width": "100", "max_height": "100", "x_offset": "0", "y_offset": "-25"}
    }
}

class Config:
    def __init__(self, skin_path: str):
        self.config = configparser.ConfigParser()
        self.skin_path = skin_path
        self.config_file = os.path.join(skin_path, "config.ini")
        
        # Load or create config
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
        try:
            with open(self.config_file, 'w') as configfile:
                self.config.write(configfile)
            logger.info(f"Configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
    
    def get_audio_config(self) -> Dict[str, Any]:
        section = self.config['Audio']
        return {
            'rate': int(section.get('rate')),
            'chunk': int(section.get('chunk')),
            'channels': int(section.get('channels')),
            'threshold': int(section.get('threshold')),
            'silence_timeout': float(section.get('silence_timeout'))
        }
    
    def get_thresholds(self) -> Dict[str, int]:
        section = self.config['Thresholds']
        return {
            'default': int(section.get('default')),
            'talking': int(section.get('talking')),
            'screaming': int(section.get('screaming'))
        }
    
    def get_display_config(self, mode: str = "default") -> Dict[str, Any]:
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
        return [section[8:] for section in self.config.sections() 
                if section.startswith("Display_")]

@contextmanager
def audio_stream(p_audio, **kwargs):
    """Context manager for audio stream to ensure proper resource cleanup"""
    stream = p_audio.open(**kwargs)
    try:
        yield stream
    finally:
        if stream:
            stream.stop_stream()
            stream.close()

class AudioProcessor:
    def __init__(self, config: Config, callback: Callable[[str], None]):
        self.audio_config = config.get_audio_config()
        self.thresholds = config.get_thresholds()
        self.callback = callback
        self.running = False
        self.p = None
        self.thread = None
        self.last_active_time = 0
        self.current_mode = "default"
    
    def start(self) -> None:
        """Start the audio processing thread"""
        if self.thread and self.thread.is_alive():
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._audio_detection_thread)
        self.thread.daemon = True
        self.thread.start()
        logger.info("Audio detection thread started")
    
    def stop(self) -> None:
        """Stop the audio processing thread and clean up resources"""
        if not self.running:
            return
            
        self.running = False
        
        if self.thread and self.thread.is_alive():
            self.thread.join(1.0)
            
        logger.info("Audio processor stopped")
    
    def _audio_detection_thread(self) -> None:
        """Thread function to detect audio input"""
        try:
            self.p = pyaudio.PyAudio()
            
            stream_params = {
                'format': pyaudio.paInt16,
                'channels': self.audio_config['channels'],
                'rate': self.audio_config['rate'],
                'input': True,
                'frames_per_buffer': self.audio_config['chunk']
            }
            
            with audio_stream(self.p, **stream_params) as stream:
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
                        new_mode = "default"
                        if volume > self.thresholds['screaming']:
                            new_mode = "screaming"
                        elif volume > self.thresholds['talking']:
                            new_mode = "talking"
                        
                        # Check if mode has changed
                        if new_mode != "default":
                            self.last_active_time = time.time()
                            if self.current_mode != new_mode:
                                self.current_mode = new_mode
                                GLib.idle_add(self.callback, new_mode)
                        elif (time.time() - self.last_active_time) > silence_timeout and self.current_mode != "default":
                            self.current_mode = "default"
                            GLib.idle_add(self.callback, "default")
                            
                    except IOError as e:
                        logger.warning(f"Audio stream IOError: {e}")
                        time.sleep(0.1)
                    except Exception as e:
                        logger.error(f"Error in audio detection: {e}")
                        time.sleep(0.5)
                        
        except Exception as e:
            logger.error(f"Fatal error in audio thread: {e}")
        finally:
            if self.p:
                try:
                    self.p.terminate()
                except:
                    pass
                self.p = None

class TransparentWindow(Gtk.Window):
    def __init__(self, config: Config):
        Gtk.Window.__init__(self, title="Anilay")
        
        # Store configuration
        self.config = config
        self.current_mode = "default"
        self.display_config = self.config.get_display_config(self.current_mode)
        
        # Set up window properties
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_resizable(False)
        self.stick()  # Make visible across all workspaces
        
        # Make window transparent
        self.set_app_paintable(True)
        self.on_screen_changed(None)
        
        # Connect signals
        self.connect("draw", self.on_draw)
        self.connect("destroy", self.on_destroy)
        self.connect("screen-changed", self.on_screen_changed)
        self.connect("button-press-event", self.on_button_press)
        
        # Create image container and add to window
        self.image = Gtk.Image()
        self.add(self.image)
        
        # Initialize state
        self.frame_timeout_id = None
        self.current_image_path = None
        self.running = False
        self.initial_position_set = False
        self.base_position = (0, 0)  # Base position to return to after state changes
        self.current_position = (0, 0)  # Current position with offsets applied
        
        # Initialize audio processor
        self.audio_processor = AudioProcessor(self.config, self.set_mode)
        
        # Load the default image and position window
        self.load_image(self.display_config['image'])

    def on_screen_changed(self, widget, previous_screen=None) -> bool:
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        return True

    def on_draw(self, widget, cr) -> bool:
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(1)  # Clear operator
        cr.paint()
        return False
    
    def on_button_press(self, widget, event) -> bool:
        if event.button == 1:  # Left mouse button
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            # Update our stored position after the move completes
            GLib.timeout_add(100, self._update_position_after_drag)
        return True
        
    def _update_position_after_drag(self) -> bool:
        """Update stored position after user drags the window"""
        if self.get_window() and self.get_window().is_visible():
            self.base_position = self.get_position()
            self.current_position = self.base_position
            logger.debug(f"Updated base position to {self.base_position} after drag")
        return False  # Don't repeat

    def load_image(self, image_path: str) -> None:
        """Load an image or animation from the given path"""
        if self.current_image_path == image_path:
            return  # Don't reload if it's the same image
            
        self.current_image_path = image_path
        
        try:
            path = Path(image_path)
            if not path.exists():
                logger.error(f"Image file not found: {image_path}")
                return
                
            if path.suffix.lower() == '.gif':
                animation = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
                self.image.set_from_animation(animation)
                
                # Handle animation if needed
                if not animation.is_static_image():
                    self._start_animation_loop(animation.get_iter(None))
                else:
                    # Static GIF
                    pixbuf = animation.get_static_image()
                    pixbuf = self._scale_pixbuf(pixbuf)
                    self.image.set_from_pixbuf(pixbuf)
                    self.resize(pixbuf.get_width(), pixbuf.get_height())
            else:
                # Regular static image
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
                pixbuf = self._scale_pixbuf(pixbuf)
                self.image.set_from_pixbuf(pixbuf)
                self.resize(pixbuf.get_width(), pixbuf.get_height())
            
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

    def _scale_pixbuf(self, pixbuf) -> GdkPixbuf.Pixbuf:
        """Scale the pixbuf to fit within max dimensions"""
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        
        max_width = self.display_config['max_width']
        max_height = self.display_config['max_height']

        # Calculate scale factor to fit within max dimensions
        scale_factor = min(max_width / width, max_height / height)
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        return pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)

    def _start_animation_loop(self, iter) -> None:
        """Start the animation loop for GIFs"""
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
        """Position the window based on display configuration
        
        Args:
            initial_placement: If True, places window at default position.
                              If False, adjusts position relative to current position.
        """
        display = Gdk.Display.get_default()
        
        # Get the monitor geometry
        if self.get_window() and self.get_window().is_visible():
            monitor = display.get_monitor_at_window(self.get_window())
        else:
            monitor = display.get_monitor(0)
        
        geometry = monitor.get_geometry()
        win_width, win_height = self.get_size()
        
        if initial_placement or not self.get_window() or not self.get_window().is_visible():
            # Initial placement based on monitor geometry
            x = geometry.x + geometry.width - win_width - self.display_config['x_offset']
            y = geometry.y + geometry.height - win_height - self.display_config['y_offset']
            
            # Store this as our base position
            self.base_position = (x, y)
            self.current_position = (x, y)
            
            self.move(x, y)
            logger.debug(f"Set initial position to {x}, {y}")
        else:
            # Apply offsets for the current mode
            self.apply_offset_for_mode()

    def apply_offset_for_mode(self) -> None:
        """Apply the x and y offsets for the current mode"""
        # Get the offsets for the current mode
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
        """Set the display mode and update the image"""
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
    
    def on_destroy(self, widget) -> None:
        """Handle window destruction"""
        self.cleanup()
        Gtk.main_quit()

    def start(self) -> None:
        """Start the window and audio processor"""
        self.show_all()
        self.running = True
        self.audio_processor.start()

    def cleanup(self) -> None:
        """Cleanup resources on exit"""
        logger.info("Cleaning up resources...")
        self.running = False
        
        # Stop audio processing
        if hasattr(self, 'audio_processor'):
            self.audio_processor.stop()
        
        # Remove any pending animation timeouts
        if self.frame_timeout_id:
            GLib.source_remove(self.frame_timeout_id)
            self.frame_timeout_id = None

def setup_signal_handlers(window: TransparentWindow) -> None:
    """Setup signal handlers for proper termination"""
    if hasattr(GLib, 'unix_signal_add'):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, lambda: (window.destroy(), True))  # SIGINT
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, lambda: (window.destroy(), True))  # SIGTERM

def main() -> None:
    """Main function to parse arguments and start the application"""
    # Set application name for proper window management
    GLib.set_application_name("Anilay")
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Anilay - Shows a visual indicator when microphone detects sound")
    parser.add_argument('--skin', required=True, help="Path to the skin folder")
    args = parser.parse_args()
    
    # Validate skin path
    skin_path = os.path.abspath(args.skin)
    if not os.path.isdir(skin_path):
        logger.error(f"Skin folder not found: {skin_path}")
        sys.exit(1)
    
    # Ensure the skin folder ends with .al
    if not skin_path.endswith('.al'):
        logger.error(f"Skin folder must end with '.al': {skin_path}")
        sys.exit(1)
    
    logger.info(f"Using skin: {skin_path}")
    
    # Initialize configuration
    config = Config(skin_path)
    
    # Initialize and run
    window = TransparentWindow(config)
    
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