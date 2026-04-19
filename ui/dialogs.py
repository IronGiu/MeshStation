import tkinter as tk
from tkinter import scrolledtext, Canvas
import sys
from utils.paths import get_resource_path

def show_fatal_error(title: str, message: str, PROGRAM_NAME="MeshStation"):
    """Show a fatal error dialog with copyable text, works before NiceGUI starts on all platforms."""
    try:
        BG      = "#f8f8f8"
        FG      = "#1a1a1a"
        FG_ERR  = "#c0392b"
        BTN_BG  = "#e0e0e0"

        root = tk.Tk()
        root.configure(bg=BG)
        root.title(title)
        root.attributes('-topmost', True)
        root.resizable(True, True)
        root.minsize(480, 280)

        # Center on screen
        root.update_idletasks()
        w, h = 520, 320
        x = (root.winfo_screenwidth() // 2) - (w // 2)
        y = (root.winfo_screenheight() // 2) - (h // 2)
        root.geometry(f"{w}x{h}+{x}+{y}")

        try:
            icon_path = get_resource_path("icon.ico")
            if icon_path.endswith(".ico"):
                root.iconbitmap(icon_path)
        except Exception:
            pass

        main_frame = tk.Frame(root, bg=BG, padx=20, pady=20)
        main_frame.pack(expand=True, fill='both')

        header_label = tk.Label(
            main_frame, text="Fatal Error Occurred",
            fg=FG_ERR, bg=BG, font=("Segoe UI", 14, "bold")
        )
        header_label.pack(anchor='w', pady=(0, 10))

        msg_area = scrolledtext.ScrolledText(
            main_frame, wrap=tk.WORD, bg=BG, fg=FG,
            font=("Segoe UI", 10), bd=0, highlightthickness=0
        )
        msg_area.insert(tk.INSERT, message)
        msg_area.configure(state='disabled')
        msg_area.pack(expand=True, fill='both', pady=(0, 20))

        def on_close():
            root.destroy()
            sys.exit(1)

        btn_close = tk.Button(
            main_frame, text="Close Application", command=on_close,
            bg=BTN_BG, fg=FG, padx=20, pady=5, bd=1, relief='flat',
            font=("Segoe UI", 10, "bold")
        )
        btn_close.pack(side='right')

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
    except Exception:
        # Last resort fallback to stderr
        print(f"\n--- FATAL ERROR: {title} ---\n{message}\n", file=sys.stderr)
        sys.exit(1)
