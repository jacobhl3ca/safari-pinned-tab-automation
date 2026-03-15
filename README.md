# Safari Pinned Tab Automation

A Python automation script for macOS that efficiently manages Safari pinned tabs. Perfect for bulk operations when you need to unpin or close multiple pinned tabs quickly.

## 💡 Why This Exists

Safari doesn't support batch unpin natively, and unpinning a tab causes remaining pinned tabs to shift position, breaking forward navigation. Simple sequential automation (right-click, unpin, move to next) fails because after each unpin, the tabs slide left and your cursor lands on the wrong tab. Closing pinned tabs has a similar issue — the context menu option count differs from unpinning, so the same automation can't handle both without accounting for that. This script solves these quirks with bidirectional navigation (left-to-right lets tabs auto-shift toward the cursor, right-to-left manually moves to each next tab) and separate handling for unpin vs. close operations.

## 🚀 Features

- **Dual Operations**: Support for both unpinning and closing pinned tabs
- **Bidirectional Navigation**: Work left-to-right or right-to-left
- **Smart Movement**: Automatically handles tab position shifts during operations
- **User-Friendly Interface**: Clear prompts and visual indicators
- **Fast Execution**: Optimized timing for quick tab management

## 📋 How It Works

### Operations
1. **Unpin Tab**: Removes the pin status from tabs (1 down arrow press)
2. **Close Tab**: Completely closes pinned tabs (3 down arrow presses)

### Direction Logic
- **Left to Right**: Mouse stays in position - tabs automatically shift left when processed
- **Right to Left**: Mouse moves left to each next tab manually

## 🛠️ Setup

1. Clone or download the repository
2. Create and activate virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## 🎯 Usage

### Quick Start
```bash
cd /path/to/project
source venv/bin/activate
python safari_pinned_tab_automation.py
```

### Step-by-Step
1. Run the script
2. Choose your operation:
   - `1` for Unpin Tab
   - `2` for Close Tab
3. Choose direction:
   - `1` for <-- Right to left (Recommended)
   - `2` for --> Left to right
4. Position your mouse on the first pinned tab
5. Press ENTER to start
6. Press Ctrl+C to stop anytime

## ⚙️ Configuration

Adjust these settings in `safari_pinned_tab_automation.py`:

- `tab_distance`: Distance between Safari pinned tabs (default: 36 pixels)
- `pyautogui.PAUSE`: Global pause between actions (default: 0.1 seconds)

## 🛡️ Safety Features

- **Emergency Stop**: Move mouse to top-left corner of screen
- **Failsafe**: PyAutoGUI's built-in protection
- **User Confirmation**: Must press ENTER to start

## 📦 Requirements

- macOS
- Python 3.6+
- Safari browser
- Accessibility permissions for Terminal/Python (prompted on first run)

## 🔧 Dependencies

- `pyautogui` - Mouse and keyboard automation

## 🐛 Troubleshooting

**Script Not Working**:
- Check that mouse is positioned correctly on first tab before pressing ENTER
- Verify tab distance matches your Safari setup

**Stop Method**:
- **Mouse Stop**: Move mouse to top-left corner of screen

## 📄 License

MIT License - feel free to use and modify for your own automation needs!

## 🤝 Contributing

Issues and pull requests are welcome! This is a practical automation tool for everyday Safari users.
