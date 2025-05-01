#!/usr/bin/env python3

# Needed imports
import os, gi, sys, time, pyaudio, logging, argparse, threading, numpy as np, configparser
from pathlib import Path

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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
        "talking": { "image": "talking.png", "max_width": "100", "max_height": "100", "x_offset": "50", "y_offset": "50"},
        "screaming": {"image": "screaming.png", "max_width": "100", "max_height": "100", "x_offset": "50", "y_offset": "50"}
    }
}

# Configuration class to handle reading and writing config files
class Config:
    def __init__(self, skin_path):
        self.config = configparser.ConfigParser()
        self.skin_path = skin_path
        self.config_file = os.path.join(skin_path, "config.ini")
        
        # Load or create config
        if os.path.exists(self.config_file):
            try:
                self.config.read(self.config_file)
                logger.info(f"Loaded configuration from {self.config_file}")
                self._validate_config()
            except Exception as e:
                logger.error(f"Error reading config: {e}")
                self._set_defaults()
                self._save_config()
        else:
            logger.warning(f"Config file not found at {self.config_file}. Creating default config.")
            self._set_defaults()
            self._save_config()
    
    # Validate the configuration to ensure all necessary sections and options are present
    def _validate_config(self):
        # Check if config has all necessary sections and options
        needs_update = False
        
        # Check for required sections
        for section, values in DEFAULT_CONFIG.items():
            if section == "Display":
                # Handle nested Display section
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
                # Handle regular sections
                if not self.config.has_section(section):
                    self.config.add_section(section)
                    needs_update = True
                for key, value in values.items():
                    if not self.config.has_option(section, key):
                        self.config.set(section, key, value)
                        needs_update = True
        
        if needs_update:
            self._save_config()
    
    # Set default values for the configuration
    def _set_defaults(self):
        for section, values in DEFAULT_CONFIG.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
            
            if section == "Display":
                # Handle nested structure for Display section
                for mode, mode_values in values.items():
                    if not self.config.has_section(f"Display_{mode}"):
                        self.config.add_section(f"Display_{mode}")
                    for key, value in mode_values.items():
                        self.config.set(f"Display_{mode}", key, value)
            else:
                # Handle regular sections
                for key, value in values.items():
                    self.config.set(section, key, value)
    
    # Save the configuration to file
    def _save_config(self):
        try:
            with open(self.config_file, 'w') as configfile:
                self.config.write(configfile)
            logger.info(f"Configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
    
    # Getters for configuration values
    def get_audio_config(self):
        section = self.config['Audio']
        return {
            'rate': int(section.get('rate')),
            'chunk': int(section.get('chunk')),
            'channels': int(section.get('channels')),
            'threshold': int(section.get('threshold')),
            'silence_timeout': float(section.get('silence_timeout'))
        }
    
    # Get thresholds for different modes
    def get_thresholds(self):
        section = self.config['Thresholds']
        return {
            'default': int(section.get('default')),
            'talking': int(section.get('talking')),
            'screaming': int(section.get('screaming'))
        }
    
    # Get display configuration for a specific mode
    def get_display_config(self, mode="default"):
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
    
    # Get all available display modes
    def get_all_display_modes(self):
        return [section[8:] for section in self.config.sections() 
                if section.startswith("Display_")]

# AudioProcessor class to handle audio input and detection
class AudioProcessor:    
    def __init__(self, config, callback):
        self.audio_config = config.get_audio_config()
        self.thresholds = config.get_thresholds()
        self.callback = callback
        self.running = False
        self.stream = None
        self.p = None
        self.thread = None
        self.last_active_time = 0
        self.current_mode = "default"
    
    # Start the audio processing thread
    def start(self):
        if self.thread and self.thread.is_alive():
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._audio_detection_thread)
        self.thread.daemon = True
        self.thread.start()
        logger.info("Audio detection thread started")
    
    # Stop the audio processing thread
    def stop(self):
        self.running = False
        
        # Close audio stream if open
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
        
        # Terminate PyAudio instance
        if self.p:
            try:
                self.p.terminate()
            except Exception as e:
                logger.error(f"Error terminating PyAudio: {e}")
        
        # Wait for thread to finish
        if self.thread and self.thread.is_alive():
            self.thread.join(1.0)
            
        logger.info("Audio processor stopped")
    
    # Thread function to detect audio input
    def _audio_detection_thread(self):
        try:
            self.p = pyaudio.PyAudio()
            
            # Open audio stream
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=self.audio_config['channels'],
                rate=self.audio_config['rate'],
                input=True,
                frames_per_buffer=self.audio_config['chunk']
            )
            
            # Get config values
            chunk_size = self.audio_config['chunk']
            silence_timeout = self.audio_config['silence_timeout']
            
            while self.running:
                try:
                    # Read audio data
                    data = np.frombuffer(
                        self.stream.read(chunk_size, exception_on_overflow=False), 
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
            self._cleanup_audio()
    
    # Cleanup audio resources
    def _cleanup_audio(self):
        if hasattr(self, 'stream') and self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass
        if hasattr(self, 'p') and self.p:
            try:
                self.p.terminate()
            except:
                pass

# TransparentWindow class to create a transparent GTK window
class TransparentWindow(Gtk.Window):
    def __init__(self, config):
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
        self.on_screen_changed(self, None)
        
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
        
        # Initialize audio processor
        self.audio_processor = AudioProcessor(self.config, self.set_mode)
        
        # Load the default image and position window
        self.load_image(self.display_config['image'])
        self.position_window()

    # Handle screen changes to maintain transparency
    def on_screen_changed(self, widget, previous_screen):
        screen = widget.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        return True

    # Handle drawing to maintain transparency
    def on_draw(self, widget, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(1)  # Clear operator
        cr.paint()
        return False
    
    # Handle mouse button press events for repositioning
    def on_button_press(self, widget, event):
        if event.button == 1:  # Left mouse button
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    #  Load an image or animation
    def load_image(self, image_path):
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
            
        except GLib.Error as e:
            logger.error(f"Error loading image {image_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading image {image_path}: {e}")

    # Scale the pixbuf to fit within max dimensions
    def _scale_pixbuf(self, pixbuf):
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        
        max_width = self.display_config['max_width']
        max_height = self.display_config['max_height']

        # Calculate scale factor to fit within max dimensions, scaling up or down
        scale_factor = min(max_width / width, max_height / height)
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
        
        return pixbuf

    # Start the animation loop for GIFs
    def _start_animation_loop(self, iter):
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
        self.running = True
        update_animation()

    # Position the window based on display configuration
    def position_window(self, force=False):
        if not force and self.get_window() and self.get_window().is_visible():
            # If the window is already visible and not forced, don't reposition
            return

        display = Gdk.Display.get_default()
        
        # Get the monitor geometry
        if self.get_window():
            monitor = display.get_monitor_at_window(self.get_window())
        else:
            monitor = display.get_monitor(0)
        
        geometry = monitor.get_geometry()
        
        win_width, win_height = self.get_size()
        x = geometry.x + geometry.width - win_width - self.display_config['x_offset']
        y = geometry.y + geometry.height - win_height - self.display_config['y_offset']
        
        self.move(x, y)

    # Set the display mode and update the image
    def set_mode(self, mode):
        if mode == self.current_mode:
            return

        self.current_mode = mode
        self.display_config = self.config.get_display_config(mode)
        self._update_image_state()

    # Update the image and position based on the current mode
    def _update_image_state(self):
        # Stop animation if playing
        if self.frame_timeout_id:
            GLib.source_remove(self.frame_timeout_id)
            self.frame_timeout_id = None
            
        self.load_image(self.display_config['image'])
        self.position_window()  # Reposition after load
        return False
    
    # Handle window destruction
    def on_destroy(self, widget):
        self.cleanup()
        Gtk.main_quit()

    # Start the window and audio processor
    def start(self):
        self.show_all()
        self.audio_processor.start()
        self.running = True

    # Cleanup resources on exit
    def cleanup(self):
        logger.info("Cleaning up resources...")
        self.running = False
        
        # Stop audio processing
        if hasattr(self, 'audio_processor'):
            self.audio_processor.stop()
        
        # Remove any pending animation timeouts
        if self.frame_timeout_id:
            GLib.source_remove(self.frame_timeout_id)
            self.frame_timeout_id = None

# Main function to parse arguments and start the application
def main():
    # Set application name for proper window management
    GLib.set_application_name("Anilay")
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Anilay - Shows a visual indicator when microphone detects sound")
    parser.add_argument('--skin', required=True, help="Path to the skin folder")
    args = parser.parse_args()
    
    # Validate skin path
    skin_path = os.path.abspath(args.skin)
    if not os.path.isdir(skin_path):
        print(f"Error: Skin folder not found: {skin_path}")
        sys.exit(1)
    
    # Ensure the skin folder ends with .al
    if not skin_path.endswith('.al'):
        print(f"Error: Skin folder must end with '.al': {skin_path}")
        sys.exit(1)
    
    logger.info(f"Using skin: {skin_path}")
    
    # Initialize configuration
    config = Config(skin_path)
    
    # Initialize and run
    win = TransparentWindow(config)
    
    try:
        # Setup signal handlers for proper termination
        if hasattr(GLib, 'unix_signal_add'):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, lambda: (win.destroy(), True))  # SIGINT
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, lambda: (win.destroy(), True))  # SIGTERM
        
        win.start()
        Gtk.main()
    except KeyboardInterrupt:
        win.destroy()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        win.cleanup()

# Run the main function if this script is executed directly
if __name__ == "__main__":
    main()