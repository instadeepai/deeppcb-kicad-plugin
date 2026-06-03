# DeepPCB KiCad Plugin

AI-powered PCB routing plugin for KiCad. Route your board automatically using the DeepPCB cloud engine — directly from the KiCad PCB editor.

https://github.com/user-attachments/assets/198d1afe-c94c-4cfe-8b52-d0bf90b094cf



## Features

- **AI-powered routing** — Automatic PCB routing driven by DeepPCB's cloud engine
- **Credit-based system** — Pay only for what you use, with real-time balance tracking
- **Session restore** — Resume interrupted routing jobs without starting over
- **Multi-platform** — Works on Windows, macOS, and Linux

## Requirements

- **KiCad**: Version 6.0 or later
- **Python**: Version 3.x (bundled with KiCad)
- **Platforms**: Windows, macOS, Linux
- **DeepPCB account**: Sign up at [app.deeppcb.ai](https://app.deeppcb.ai)

## Installation

### Plugin and Content Manager (Recommended)

1. Open **KiCad**
2. Go to **Plugin and Content Manager** (PCM)
   - Windows/Linux: `Tools` > `Plugin and Content Manager`
   - macOS: `KiCad` > `Plugin and Content Manager`
3. Search for **DeepPCB** and click **Install**
4. Click **Apply Pending Changes**
5. Restart KiCad

### Install from File

1. Download the latest `.zip` from the [Releases](https://github.com/instadeep/deeppcb-kicad-plugin/releases) page
2. In KiCad, open **Plugin and Content Manager**
3. Click **Install from File...**
4. Select the downloaded `.zip`
5. Click **Apply Pending Changes**
6. Restart KiCad

### Manual Installation

Extract the `.zip` and copy the `plugins` folder contents to the KiCad scripting plugins directory:

**Windows**
```
%USERPROFILE%\Documents\KiCad\10.0\scripting\plugins\
```

**macOS**
```
~/Documents/KiCad/10.0/scripting/plugins/
```

**Linux**
```
~/.local/share/kicad/10.0/scripting/plugins/
```

> Adjust the version number (`10.0`) to match your KiCad installation.

## Usage

1. Open a PCB in **Pcbnew** (KiCad PCB Editor)
2. Launch the plugin from `Tools` > `External Plugins` > **DeepPCB**
3. Enter your API key (first time only — get one from [app.deeppcb.ai/integration](https://app.deeppcb.ai/integration))
4. Click **Create Board** to start a routing job
5. Monitor progress in the plugin panel
6. Once complete, click **Download** to import the routed board back into your project

## Configuration

Your API key and settings are stored locally:

```
~/.kicad/deeppcb/config.ini
```

To change your API key, use the plugin's settings panel or edit the config file directly.

## Building from Source

The plugin can be packaged into a `.zip` archive for distribution.

```bash
# Default build
python build_package.py

# Custom output filename and directory
python build_package.py -o my-build -d ./dist

# Print version
python build_package.py --version

# Preview metadata
python build_package.py --metadata
```

### Package Structure

```
deeppcb-kicad-plugin-v{version}.zip
├── metadata.json
├── resources/
│   └── icon.png
└── plugins/
    ├── __init__.py
    ├── config.py
    ├── custom_widgets.py
    ├── deeppcb_plugin.py
    ├── parser.py
    ├── utils.py
    ├── assets/
    ├── dialogs/
    ├── helpers/
    └── panels/
```

## License

Apache-2.0
