import os
import glob
import ctypes
import re
import sys
import configparser
import numpy as np
import matplotlib.pyplot as plt
import tifffile as tiff
import piexif
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image as PILImage
from scipy.ndimage import gaussian_filter
from tqdm import tqdm
import subprocess

# ==========================================
# 1. KONFIGURACJA
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser()
config.read(os.path.join(BASE_DIR, 'termika.conf'), encoding='utf-8')

DJI_LIBS_PATH = config.get('USTAWIENIA', 'dji_libs_path')
LOGO_NAME = config.get('USTAWIENIA', 'logo_name')
LOGO_SIZE = config.getint('USTAWIENIA', 'logo_size')
INPUT_DIR = config.get('USTAWIENIA', 'input_dir')
OUTPUT_DIR = config.get('USTAWIENIA', 'output_dir')
JEDNOSTKA = config.get('USTAWIENIA', 'jednostka_nazwa')
STREFA_DOL_P = config.getfloat('USTAWIENIA', 'strefa_dol_procent') / 100.0
STREFA_GORA_P = config.getfloat('USTAWIENIA', 'strefa_gora_procent') / 100.0

LOGO_FULL_PATH = os.path.join(BASE_DIR, LOGO_NAME)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ==========================================
# 2. MONKEYPATCH DJI SDK
# ==========================================
main_lib_path = os.path.join(DJI_LIBS_PATH, "libdirp.so")
original_cdll = ctypes.CDLL
def patched_cdll(name, *args, **kwargs):
    if name and ("dirp" in name or "iirp" in name):
        full_path = os.path.join(DJI_LIBS_PATH, name if name.endswith(".so") else f"{name}.so")
        return original_cdll(full_path if os.path.exists(full_path) else main_lib_path, *args, **kwargs)
    return original_cdll(name, *args, **kwargs)
ctypes.CDLL = patched_cdll

from thermal_parser import Thermal
thermal_engine = Thermal(dtype=np.float32)

# ==========================================
# 3. FUNKCJE PRZETWARZANIA
# ==========================================

def save_radiometric_tiff(source_jpg, data, output_path):
    """Zapisuje dane float32 i kopiuje komplet metadanych DJI za pomocą ExifTool."""
    try:
        # KROK 1: Zapis surowych danych (czysty TIFF bez metadanych)
        tiff.imwrite(
            output_path, 
            data.astype(np.float32), 
            photometric='minisblack',
            description='DJI Radiometric Thermal Data'
        )

        # KROK 2: Kopiowanie wszystkich metadanych (GPS, XMP, RTK, EXIF)
        # -TagsFromFile: bierzemy tagi z pliku źródłowego
        # -all:all: kopiujemy wszystkie możliwe grupy metadanych
        # -unsafe: kopiujemy tagi systemowe (często używane przez DJI do RTK)
        # -overwrite_original: nie tworzy pliku kopii .tif_original
        result = subprocess.run([
            'exiftool', '-overwrite_original', 
            '-TagsFromFile', source_jpg, 
            '-all:all', '-unsafe', 
            output_path
        ], capture_output=True, text=True)

        if result.returncode != 0:
            print(f"\n[!] ExifTool ostrzeżenie dla {os.path.basename(source_jpg)}: {result.stderr}")

    except Exception as e:
        print(f"\n[BŁĄD KRYTYCZNY] Podczas zapisu {output_path}: {e}")

def process_image(filepath, logo_img, mode):
    fname = os.path.basename(filepath)
    temp_data = thermal_engine.parse(filepath_image=filepath)
    
    # TRYB ORTO: Tylko TIFF, żadnej grafiki
    if mode == "orto":
        output_path = os.path.join(OUTPUT_DIR, fname.replace('.JPG', '.tif'))
        save_radiometric_tiff(filepath, temp_data, output_path)
        return

    # TRYB RAPORT (Podstawowy lub Strefowy)
    use_zones = (mode == "strefa")
    date_str = parse_dji_datetime(fname)
    sensor_model = get_sensor_info(filepath)
    h, w = temp_data.shape
    t_min, t_max = np.min(temp_data), np.max(temp_data)
    t_avg, t_med = np.mean(temp_data), np.median(temp_data)
    
    fig = plt.figure(figsize=(10, 11), facecolor='white')
    ax = fig.add_axes([0, 0.25, 1, 0.75]) 
    ax.axis('off')
    img_plot = ax.imshow(temp_data, cmap='inferno', aspect='auto')
    
    zone_info = ""
    if use_zones:
        t_low, t_high = t_med * (1 - STREFA_DOL_P), t_med * (1 + STREFA_GORA_P)
        mask = np.logical_and(temp_data >= t_low, temp_data <= t_high).astype(float)
        if np.min(mask) != np.max(mask):
            smoothed_mask = gaussian_filter(mask, sigma=1.5)
            if np.min(smoothed_mask) < 0.5 < np.max(smoothed_mask):
                ax.contour(smoothed_mask, levels=[0.5], colors='red', linewidths=0.6)
        zone_info = f" | Zakres w strefie: {t_low:.1f}-{t_high:.1f}°C"

    max_p = np.unravel_index(np.argmax(temp_data), temp_data.shape)
    min_p = np.unravel_index(np.argmin(temp_data), temp_data.shape)
    ax.scatter(max_p[1], max_p[0], color='red', marker='+', s=150)
    ax.text(max_p[1], max_p[0]-25, f"{t_max:.1f}°C", color='white', weight='bold', ha='center', size=9, 
            bbox=dict(facecolor='red', alpha=0.6, edgecolor='none', boxstyle='round'))
    ax.scatter(min_p[1], min_p[0], color='cyan', marker='+', s=150)
    ax.text(min_p[1], min_p[0]+45, f"{t_min:.1f}°C", color='black', weight='bold', ha='center', size=9, 
            bbox=dict(facecolor='cyan', alpha=0.6, edgecolor='none', boxstyle='round'))

    if logo_img is not None:
        ax.add_artist(AnnotationBbox(OffsetImage(logo_img, zoom=1), (0.97, 0.96), xycoords='axes fraction', box_alignment=(1, 1), frameon=False))

    plt.Line2D([0.02, 0.98], [0.22, 0.22], transform=fig.transFigure, color='black', linewidth=1.5)
    fig.lines.append(plt.Line2D([0.02, 0.98], [0.22, 0.22], transform=fig.transFigure, color='black'))
    fig.text(0.04, 0.16, JEDNOSTKA, fontsize=15, fontweight='bold')
    fig.text(0.04, 0.11, f"Sensor: {sensor_model} ({w}x{h})  |  Data: {date_str}{zone_info}", fontsize=11)
    stats_text = f"Pomiar:  MIN: {t_min:.1f}°C  |  MAX: {t_max:.1f}°C  |  Ś©ednia: {t_avg:.1f}°C  |  Mediana: {t_med:.1f}°C"
    fig.text(0.04, 0.05, stats_text, fontsize=12, fontweight='bold', bbox=dict(facecolor='none', edgecolor='black', pad=5))
    plt.colorbar(img_plot, cax=fig.add_axes([0.93, 0.35, 0.015, 0.45])).set_label('°C')

    output_path = os.path.join(OUTPUT_DIR, fname.replace('.JPG', f'_{mode}.jpg'))
    plt.savefig(output_path, dpi=200, facecolor='white')
    plt.close(fig)

# (Pomocnicze funkcje get_sensor_info i parse_dji_datetime pozostają bez zmian)
def get_sensor_info(filepath):
    try:
        with PILImage.open(filepath) as img:
            exif = img._getexif()
            if exif: return exif.get(272, "Nieznany Sensor").strip()
    except: pass
    return "Nieznany Sensor"

def parse_dji_datetime(filename):
    match = re.search(r'(\d{8})(\d{6})', filename)
    if match:
        d, t = match.groups()
        return f"{d[6:8]}.{d[4:6]}.{d[0:4]} {t[0:2]}:{t[2:4]}:{t[4:6]}"
    return "Data nieznana"

# ==========================================
# 4. GŁÓWNA PĘTLA
# ==========================================
if __name__ == "__main__":
    current_mode = "podstawa"
    if "-strefa" in sys.argv: current_mode = "strefa"
    if "-orto" in sys.argv:   current_mode = "orto"
    
    files = sorted(glob.glob(os.path.join(INPUT_DIR, '*_T.JPG')))
    logo_data = None
    if os.path.exists(LOGO_FULL_PATH) and current_mode != "orto":
        logo_data = np.array(PILImage.open(LOGO_FULL_PATH).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE)))
    
    print(f"TRYB PRACY: {current_mode.upper()}")
    for f in tqdm(files):
        try:
            process_image(f, logo_data, current_mode)
        except Exception as e:
            print(f"\n[BŁĄD] {os.path.basename(f)}: {e}")
