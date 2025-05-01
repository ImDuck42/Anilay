# Anilay - Animated (Audio) Overlay

![Analay Logo](/assets/logo.svg)

Anilay is a desktop application written in Python that displays animated overlays that react to your microphone input.  
Perfect for streamers, content creators, and anyone who wants to add visual indicators when they're speaking or screaming their heart out.

## Features

- **Audio Responsive**: Shows different images based on your microphone volume
- **Multiple States**: Different visuals for quiet, talking, and screaming states
- **Customizable**: Create your own skins with custom images and positioning
- **Dynamic Movement**: Images can move around based on audio input
- **Lightweight**: Minimal CPU and memory usage
- **Platform Support**: Currently works on Linux ~~(Windows support coming soon)~~

## Installation

### Prerequisites

- Python 3.6+
- GTK3
- PyAudio

### Linux

1. Install the required system packages:

```bash
# For Debian/Ubuntu
sudo apt-get install python3-pip python3-gi python3-gi-cairo gir1.2-gtk-3.0 portaudio19-dev

# For Fedora
sudo dnf install python3-pip python3-gobject python3-cairo cairo-gobject gtk3 portaudio-devel

# For Arch Linux
sudo pacman -S python-pip python-gobject python-cairo gtk3 portaudio
```

2. Clone the repository:

```bash
git clone https://github.com/ImDuck42/Anilay.git
cd anilay
```

3. Install Python dependencies:

```bash
pip install -r /assets/requirements.txt
```

## Usage

1. Run Anilay with a skin:

```bash
python Anilay.py --skin /path/to/your/skin.al
```

2. The overlay will appear on your screen at the x, y offset of the default state and react to your microphone input.

3. You can click and drag to reposition the overlay anywhere on your screen.

## Creating Custom Skins

Skins in Anilay are folders with the `.al` extension containing images and a configuration file.  
It's enough to only have the images, a config will be auto generated if none is found.

### Basic Skin Structure

```
myskin.al/
├── config.ini
├── normal.png     # Default state image
├── talking.png    # Talking state image
└── screaming.png  # Screaming/loud state image
```

### Config File Example

```ini
[Audio]
rate = 44100
chunk = 1024
channels = 1
silence_timeout = 0.5
threshold = 50

[Thresholds]
default = 5
talking = 20
screaming = 2000

[Display]

[Display_default]
image = normal.png
max_width = 100
max_height = 100
x_offset = 0
y_offset = 0

[Display_talking]
image = talking.png
max_width = 100
max_height = 100
x_offset = 20  # Move 20 pixels right when talking
y_offset = 10  # Move 10 pixels down when talking

[Display_screaming]
image = screaming.png
max_width = 100
max_height = 100
x_offset = -30  # Move 30 pixels left when screaming
y_offset = -20  # Move 20 pixels up when screaming
```
- You can add more states yourself

### Configuration Options

#### Audio Section
- `rate`: Sample rate (Hz)
- `chunk`: Audio buffer size
- `channels`: Number of audio channels (1 for mono)
- `silence_timeout`: Time in seconds before returning to default state
- `threshold`: Base audio detection threshold

#### Thresholds Section
- `default`: Sound level for default state
- `talking`: Sound level to trigger talking state
- `screaming`: Sound level to trigger screaming state

#### Display Sections
Each mode (`default`, `talking`, `screaming`) has its own display section:

- `image`: Image file to display (PNG or GIF)
- `max_width`: Maximum width to scale the image to
- `max_height`: Maximum height to scale the image to
- `x_offset`: Horizontal offset from base position
- `y_offset`: Vertical offset from base position

### Using GIFs
You can use animated GIFs instead of static images for any state. 
Simply replace the PNG files with GIF files and update the config accordingly.

### Position Movement
The overlay's position changes based on the current audio state. Each state has defined x_offset and y_offset values in the config file, which determine how much the overlay should move relative to its base position when that state is active.

## Example Skins

Check out the `examples/` directory for sample skins you can use or modify:

- `nyx.al` - A set of images based on artwork by [QuAnCy on Pixiv](https://www.pixiv.net/en/users/23763079).

## Troubleshooting

### No Audio Detection
- Check your microphone is working properly
- Increase the threshold values in the config

### Performance Issues
- Reduce the size of your images/GIFs
- Lower the sample rate in the config
- Use less complex animations

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the project
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Acknowledgements

- [PyAudio](https://people.csail.mit.edu/hubert/pyaudio/) for audio processing
- [GTK](https://www.gtk.org/) for the GUI framework
- All the amazing contributors and testers (Me and Myself)

## Others

- `/MaybeWorking/*` contains WIP/Testing builds which have no guarantee of working at the present moment.  
    - I just included them for the sake of it

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
