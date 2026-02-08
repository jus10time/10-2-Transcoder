"""
py2app setup script for 10-2 Transcoder

To build the app:
    python setup.py py2app

For development/testing:
    python setup.py py2app -A  (alias mode - faster, links to source)

The built app will be in the 'dist' folder.
"""

from setuptools import setup
import os
import sys

# Get the path to customtkinter for including its assets
import customtkinter
customtkinter_path = os.path.dirname(customtkinter.__file__)

APP = ['run_gui.py']
APP_NAME = '10-2 Transcoder'

DATA_FILES = [
    # Include config.ini in the app bundle
    ('', ['config.ini']),
]

OPTIONS = {
    'argv_emulation': False,  # Not needed for GUI apps, can cause issues
    'iconfile': 'icon.icns',
    'plist': {
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'CFBundleIdentifier': 'com.ten2.transcoder',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # Support dark mode
        # Request full disk access for external drives
        'NSAppleEventsUsageDescription': '10-2 Transcoder needs to access files for transcoding.',
        'NSDocumentsFolderUsageDescription': '10-2 Transcoder needs access to process video files.',
        'NSRemovableVolumesUsageDescription': '10-2 Transcoder needs access to external drives for footage offloading.',
    },
    'packages': [
        'customtkinter',
        'tkinter',
        'watchdog',
        'reportlab',
        'PIL',
    ],
    'includes': [
        'main',
        'processor',
        'api_server',
        'configparser',
        'json',
        'threading',
        'queue',
        'shutil',
        'fcntl',
        'atexit',
        'logging',
        'subprocess',
    ],
    'frameworks': [],
    'resources': [
        # Include customtkinter assets (themes, etc.)
        customtkinter_path,
        # Include native FileHelper.app for external drive permissions
        # Must be launched via 'open' to get independent permission context
        'FileHelper.app',
    ],
    # Exclude unnecessary packages to reduce size
    'excludes': [
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'cv2',
        'test',
        'unittest',
    ],
}

setup(
    name=APP_NAME,
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=[],
)
