import os
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator
from typing import Iterator, TextIO

def str2bool(string):
    string = string.lower()
    str2val = {"true": True, "false": False}
    if string in str2val:
        return str2val[string]
    else:
        raise ValueError(f"Expected one of {set(str2val.keys())}, got {string}")

def format_timestamp(seconds: float, always_include_hours: bool = False):
    assert seconds >= 0, "non-negative timestamp expected"
    milliseconds = round(seconds * 1000.0)
    hours = milliseconds // 3_600_000
    milliseconds -= hours * 3_600_000
    minutes = milliseconds // 60_000
    milliseconds -= minutes * 60_000
    seconds = milliseconds // 1_000
    milliseconds -= seconds * 1_000
    hours_marker = f"{hours}:" if always_include_hours or hours > 0 else ""
    return f"{hours_marker}{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

def write_srt(
    transcript: Iterator[dict],
    file: TextIO,
    batch_size: int = 10,
    max_workers: int = 4,
    translate_to: str = "tr",
    translate_off: bool = False
):
    start_time = time.time()
    
    segments = list(transcript)
    texts = [segment.text.strip().replace('-->', '->') for segment in segments]
    
    if translate_off:
        for i, segment in enumerate(segments, start=1):
            print(
                f"{i}\n"
                f"{format_timestamp(segment.start, always_include_hours=True)} --> "
                f"{format_timestamp(segment.end, always_include_hours=True)}\n"
                f"{segment.text.strip()}\n",
                file=file,
                flush=True,
            )
    else:
        translations = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(
                    GoogleTranslator(source="auto", target=translate_to).translate_batch,
                    texts[i:i + batch_size]
                ): (i, i + batch_size)
                for i in range(0, len(texts), batch_size)
            }
            
            for future in future_to_index:
                start_idx, end_idx = future_to_index[future]
                try:
                    translations[start_idx:end_idx] = future.result()
                except Exception as e:
                    print(f"Çeviri hatası: {e}", file=file)
        
        for i, (segment, translation) in enumerate(zip(segments, translations), start=1):
            print(
                f"{i}\n"
                f"{format_timestamp(segment.start, always_include_hours=True)} --> "
                f"{format_timestamp(segment.end, always_include_hours=True)}\n"
                f"{segment.text.strip()}\n"
                f"{translation}\n",
                file=file,
                flush=True,
            )
    
    elapsed_time = time.time() - start_time
    print(f"Process completed in {int(elapsed_time // 60)}m{int(elapsed_time % 60)}s.")

def get_filename(path):
    return os.path.splitext(os.path.basename(path))[0]

def is_audio(path):
    return path.lower().endswith(('.mp3', '.wav', '.flac', '.m4a', '.wma', '.aac'))

def ffmpeg_extract_audio(input_path, output_path):
    print(f"Extracting audio from {input_path}...")
    if subprocess.run(
        ('ffmpeg', '-y', '-i', input_path, '-ac', '1', '-async', '1', output_path),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode > 0:
        raise Exception(f'Error extracting audio from {input_path}')
        
        
