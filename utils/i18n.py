import os
import sys
import json
from core.constants import LANG_FILE_NAME, PROGRAM_NAME
from utils.paths import get_languages_path, get_resource_path

# Global state for i18n
languages_data = {}
current_language = "en"

def load_languages(fatal_error_callback=None):
    global languages_data
    path = get_languages_path()
    try:
        if not os.path.exists(path):
             # Try embedded if path doesn't exist
             if getattr(sys, 'frozen', False):
                path = get_resource_path(LANG_FILE_NAME)
        
        with open(path, "r", encoding="utf-8") as f:
            languages_data = json.load(f)
    except FileNotFoundError:
        languages_data = {}
    except json.JSONDecodeError as e:
        languages_data = {}
        if fatal_error_callback:
            fatal_error_callback(
                f"{PROGRAM_NAME} — Language File Error",
                f"The language file is corrupted and cannot be parsed:\n{path}\n\nError: {e}\n\n"
                "The application will start in English.\n"
                "Please re-download the language file from the GitHub repository."
            )
    except Exception as e:
        languages_data = {}
        if fatal_error_callback:
            fatal_error_callback(
                f"{PROGRAM_NAME} — Language File Error",
                f"Could not read the language file:\n{path}\n\nError: {e}\n\n"
                "The application will start in English."
            )

def get_languages_path_wrapper():
    # Helper to avoid circular import if needed, but we already have it in paths.py
    from utils.paths import get_app_path
    base = get_app_path()
    candidate = os.path.join(base, LANG_FILE_NAME)
    if os.path.isfile(candidate):
        return candidate
    if getattr(sys, 'frozen', False):
        embedded = get_resource_path(LANG_FILE_NAME)
        if os.path.isfile(embedded):
            return embedded
    return candidate

def get_available_languages():
    if not languages_data:
        return ["en"]
    return sorted(languages_data.keys())

def translate(key: str, default: str | None = None) -> str:
    lang = current_language if current_language in languages_data else "en"
    section = languages_data.get(lang) or languages_data.get("en") or {}
    value = section.get(key)
    if value is None:
        if default is not None:
            return default
        return key
    return value

def set_current_language(lang: str):
    global current_language
    current_language = lang
