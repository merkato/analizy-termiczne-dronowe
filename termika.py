import os
import glob
import ctypes
import re
import sys
import configparser
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image as PILImage
from scipy.ndimage import gaussian_filter
from tqdm import tqdm

# ==========================================
# 1. ŁADOWANIE KONFIGURACJI
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config = configparser.ConfigParser()
config_path = os.path.join(BASE_DIR, 'termika.conf')

if not os.path.exists(config_path):
    print(f"[BŁĄD] Brak pliku konfiguracyjnego: {config_path}")
    exit()

config.read(config_path, encoding='utf-8')

# Parametry ogólne
DJI_LIBS_PATH = config.get('USTAWIENIA', 'dji_libs_path')
LOGO_NAME = config.get('USTAWIENIA', 'logo_name')
LOGO_SIZE = config.getint('USTAWIENIA', 'logo_size')
INPUT_DIR = config.get('USTAWIENIA', 'input_dir')
OUTPUT_DIR = config.get('USTAWIENIA', 'output_dir')
JEDNOSTKA = config.get('USTAWIENIA', 'jednostka_nazwa')

# Parametry strefy (nowe)
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
# 3. FUNKCJE ANALIZY
# ==========================================

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

def process_image(filepath, logo_img, use_zones=False):
    fname = os.path.basename(filepath)
    date_str = parse_dji_datetime(fname)
    sensor_model = get_sensor_info(filepath)
    
    # 1. Pobieranie danych termicznych
    temp_data = thermal_engine.parse(filepath_image=filepath)
    h, w = temp_data.shape
    t_min, t_max = np.min(temp_data), np.max(temp_data)
    t_avg, t_med = np.mean(temp_data), np.median(temp_data)
    
    # 2. Inicjalizacja wykresu
    fig = plt.figure(figsize=(10, 11), facecolor='white')
    ax = fig.add_axes([0, 0.25, 1, 0.75]) 
    ax.axis('off')
    
    img_plot = ax.imshow(temp_data, cmap='inferno', aspect='auto')
    
    # --- LOGIKA STREFY  ---
    zone_info = ""
    if use_zones:
        t_low = t_med * (1 - STREFA_DOL_P)
        t_high = t_med * (1 + STREFA_GORA_P)
        
        mask = np.logical_and(temp_data >= t_low, temp_data <= t_high).astype(float)
        
        if np.min(mask) != np.max(mask):
            smoothed_mask = gaussian_filter(mask, sigma=1.5)
            if np.min(smoothed_mask) < 0.5 < np.max(smoothed_mask):
                ax.contour(smoothed_mask, levels=[0.5], colors='red', linewidths=0.6)
        
        zone_info = f" | STREFA (M -{int(STREFA_DOL_P*100)}% / +{int(STREFA_GORA_P*100)}%): {t_low:.1f}-{t_high:.1f}°C"

    # --- CELOWNIKI + ETYKIETY ---
    max_p = np.unravel_index(np.argmax(temp_data), temp_data.shape)
    min_p = np.unravel_index(np.argmin(temp_data), temp_data.shape)

    # Max (Czerwony celownik i etykieta)
    ax.scatter(max_p[1], max_p[0], color='red', marker='+', s=150, linewidths=2)
    ax.text(max_p[1], max_p[0]-25, f"{t_max:.1f}°C", color='white', weight='bold', 
            ha='center', size=9, bbox=dict(facecolor='red', alpha=0.6, edgecolor='none', boxstyle='round,pad=0.2'))

    # Min (Błękitny celownik i etykieta)
    ax.scatter(min_p[1], min_p[0], color='cyan', marker='+', s=150, linewidths=2)
    ax.text(min_p[1], min_p[0]+45, f"{t_min:.1f}°C", color='black', weight='bold', 
            ha='center', size=9, bbox=dict(facecolor='cyan', alpha=0.6, edgecolor='none', boxstyle='round,pad=0.2'))

    # 3. Logo
    if logo_img is not None:
        ab = AnnotationBbox(OffsetImage(logo_img, zoom=1), (0.97, 0.96), 
                            xycoords='axes fraction', box_alignment=(1, 1), frameon=False)
        ax.add_artist(ab)

    # 4. Stopka
    line = plt.Line2D([0.02, 0.98], [0.22, 0.22], transform=fig.transFigure, color='black', linewidth=1.5)
    fig.lines.append(line)
    
    fig.text(0.04, 0.16, JEDNOSTKA, fontsize=15, fontweight='bold')
    fig.text(0.04, 0.11, f"Sensor: {sensor_model} ({w}x{h})  |  Data: {date_str}{zone_info}", fontsize=11)
    
    stats_text = (f"Pomiar:  MIN: {t_min:.1f}°C  |  MAX: {t_max:.1f}°C  |  "
                  f"Średnia: {t_avg:.1f}°C  |  Mediana: {t_med:.1f}°C")
    fig.text(0.04, 0.05, stats_text, fontsize=12, fontweight='bold', 
             bbox=dict(facecolor='none', edgecolor='black', pad=5))

    # Skala temperatury
    cax = fig.add_axes([0.93, 0.35, 0.015, 0.45])
    plt.colorbar(img_plot, cax=cax).set_label('°C')

    # 5. Zapis
    suffix = "_strefa" if use_zones else "_podstawa"
    output_path = os.path.join(OUTPUT_DIR, fname.replace('.JPG', f'{suffix}.jpg'))
    plt.savefig(output_path, dpi=200, facecolor='white')
    plt.close(fig)

# ==========================================
# 4. GŁÓWNA PĘTLA
# ==========================================
if __name__ == "__main__":
    STREFA_MODE = "-strefa" in sys.argv
    files = sorted(glob.glob(os.path.join(INPUT_DIR, '*_T.JPG')))
    
    logo_data = None
    if os.path.exists(LOGO_FULL_PATH):
        logo_data = np.array(PILImage.open(LOGO_FULL_PATH).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE)))
    
    print(f"Jednostka: {JEDNOSTKA}")
    print(f"TRYB: {'Strefa temperatur średnich' if STREFA_MODE else 'Analiza podstawowa'}")
    if STREFA_MODE:
        print(f"Parametry strefy: Mediana -{int(STREFA_DOL_P*100)}% / +{int(STREFA_GORA_P*100)}%")

    for f in tqdm(files):
        try:
            process_image(f, logo_data, use_zones=STREFA_MODE)
        except Exception as e:
            print(f"\n[BŁĄD] {os.path.basename(f)}: {e}")
