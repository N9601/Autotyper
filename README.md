# AutoTyper

Types text into any window as real keystrokes, with the spacing and
indentation exactly as you wrote it. Built for typing code into an editor in
a Chrome tab (LeetCode, HackerRank, Replit, vscode.dev and so on), but it
works in any app.

Windows only. Python 3 standard library only, nothing to install.

## Run it

Double-click `AutoTyper.bat`, or:

```
python autotyper.py
```

## Use it

1. Paste your code into the big box (or use **Load file...**).
2. Pick what you are typing into (see below). For a code editor in Chrome,
   keep **Code editor**.
3. Press **Pick spot & type**. AutoTyper minimizes; click where the code
   should go (an empty line in the editor). Typing starts right after the click.

Or click into the target yourself and press **F9**. That types at the
current cursor.

Or let Gemini find the spot: press **Find with Gemini & type** (see below).

**To stop:** press **Esc**, slam the mouse into the top-left corner of the
screen, or click any other window. AutoTyper also stops if a different app
takes focus while it is typing, so keystrokes never land somewhere else.

## Modes

| Mode | Use it for | What it does |
| --- | --- | --- |
| Code editor | Coding sites, VS Code, PyCharm, IDLE, also plain text boxes | Types the code and undoes what editors add on their own: auto-indent after Enter, auto-closed `)` `]` `}` and quotes, re-indenting `else:` or `}` while you type, and autocomplete popups grabbing Enter. |
| Plain text box | Notepad, chat boxes, terminals, editors with auto-indent off | Types every character exactly as is. |
| Paste (Ctrl+V) | Anywhere pasting is allowed | Copies the text and presses Ctrl+V once. Fastest. |

Options:

- **Turn tabs into spaces**: on by default. In Chrome a real Tab key jumps
  to the next field instead of indenting.
- **Press Esc to close autocomplete popups**: on by default. **Turn it off
  for Jupyter and Google Colab**, where Esc leaves the cell editor.
- **Send real key presses**: use for VMs, remote desktop or games that ignore
  normal typed characters. Characters your keyboard layout cannot produce
  still go through the normal way.
- **Delay per key**: 12 ms is a good default. Slow it down if a site drops
  characters or lags.

## Find where to type with Gemini

**Find with Gemini & type** looks at your screen and finds the box to type in,
so you do not have to click it yourself.

1. Get a free API key at <https://aistudio.google.com/apikey> and paste it
   into **API key** (or set the `GEMINI_API_KEY` environment variable).
2. In **Type into**, describe the target in plain words, for example
   `the code editor`, `the Python editor on the right`, or `the comment box`.
3. Pick what happens after the click:
   - **go to the end and type below**: jumps to the end (Ctrl+End) and, if
     Gemini saw existing code there, starts on a new line under it.
   - **replace what is there**: Ctrl+A then delete, then types. Handy for
     clearing a site's starter code.
   - **type where it clicked**: types at the click.
4. Press the button. AutoTyper minimizes, takes a screenshot of the monitor
   your target window is on, and asks Gemini where the input is. A red
   outline shows what it found. Press **Enter** (or **Type here**) to go
   ahead, or **Esc** to cancel. Untick **Ask before clicking** to skip the
   question; it then waits about a second, so you can still press Esc.

Notes:

- This sends a screenshot of that whole monitor to Google. Close anything
  private first.
- **Model** defaults to `gemini-flash-latest`, an alias Google keeps
  pointed at the newest Flash model. You can type a fixed one instead, such
  as `gemini-3.8-flash`.
- Your key is saved in `autotyper_settings.json` next to the script.
  `.gitignore` keeps that file out of git.

## Tips and limits

- Start on an empty line. Code mode clears anything to the right of the
  cursor on the lines it types.
- If a site auto-formats code as you type (rare), the result may differ.
  Turn that off in the site's editor settings or use Paste mode.
- HTML in VS Code-style editors can auto-complete closing tags when you type
  `</`. Use Paste mode for HTML if that happens.
- Editors in Vim mode are not supported.
- If the target app runs as administrator, Windows blocks the keystrokes.
  Run AutoTyper as administrator too.
- Your text and settings are saved to `autotyper_settings.json` next to the
  script when you close the app.
- Do not touch the keyboard while it types. Anything you type goes into the
  same window and mixes with the code.
