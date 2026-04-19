import os
import sys
import platform
from core.constants import PROGRAM_NAME

def get_resource_path(relative_path):
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(os.path.join(__file__, "../..")))

    # Check for PyInstaller 6+ _internal directory in onedir mode
    path = os.path.join(base_path, relative_path)
    if not os.path.exists(path):
        _internal = os.path.join(base_path, "_internal", relative_path)
        if os.path.exists(_internal):
            return _internal
    return path

def get_app_path():
    system = platform.system()
    exe_dir = os.path.dirname(sys.executable)

    if system == "Linux" and os.environ.get('APPIMAGE'):
        return os.path.dirname(os.environ.get('APPIMAGE'))

    if getattr(sys, 'frozen', False):
        if system == "Darwin":
            contents_dir = os.path.dirname(exe_dir)
            app_dir = os.path.dirname(contents_dir)
            parent_dir = os.path.dirname(app_dir)
            return parent_dir
        return exe_dir
    # When running from source in modular structure, app path is two levels up from utils/paths.py
    return os.path.dirname(os.path.abspath(os.path.join(__file__, "../..")))

def get_data_path():
    base = get_app_path()
    data_dir = os.path.join(base, "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        pass
    return data_dir

def get_autosave_path():
    base = get_data_path()
    base_name = PROGRAM_NAME.replace(" ", "")
    filename = f"{base_name}-autosave.json"
    return os.path.join(base, filename)

def get_config_path():
    base = get_data_path()
    base_name = PROGRAM_NAME.replace(" ", "")
    filename = f"Config_{base_name}.json"
    return os.path.join(base, filename)

def get_languages_path():
    from core.constants import LANG_FILE_NAME
    base = get_app_path()
    candidate = os.path.join(base, LANG_FILE_NAME)
    try:
        if os.path.isfile(candidate):
            return candidate
    except Exception:
        pass
    if getattr(sys, 'frozen', False):
        try:
            embedded = get_resource_path(LANG_FILE_NAME)
            if os.path.isfile(embedded):
                return embedded
        except Exception:
            pass
    return candidate
