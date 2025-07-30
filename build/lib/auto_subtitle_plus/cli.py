import os
import glob
import psutil
import ffmpeg
import whisper
import stable_whisper
import argparse
import warnings
import tempfile
import subprocess
import multiprocessing
import numpy as np
from torch.cuda import is_available
from .utils import get_filename, write_srt, is_audio, ffmpeg_extract_audio

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description="Auto Subtitle Plus - Automatically generate and translate subtitles for videos",
        epilog='''Examples:
  Basic usage:
    auto_subtitle_plus video.mp4 --output-video
  
  Force English transcription:
    auto_subtitle_plus video.mp4 --language en --translate-off
  
  Translate to French:
    auto_subtitle_plus video.mp4 --translate-to fr
  
  Use large model with GPU:
    auto_subtitle_plus video.mp4 --model large --device cuda'''
    )
    
    # Core arguments
    parser.add_argument("paths", nargs="+", help="Input file paths or wildcards (e.g., *.mp4)")
    
    # Model and output options
    parser.add_argument("-m", "--model", 
                       default="small",
                       choices=whisper.available_models(),
                       help="whisper model to use (default: %(default)s)")
    
    parser.add_argument("-o", "--output-dir", 
                       default=os.getcwd(),
                       help="Output directory (default: current directory)")
    
    # Output controls
    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument("-s", "--output-srt", 
                             action="store_true",
                             help="Generate SRT subtitle file")
    output_group.add_argument("-a", "--output-audio", 
                             action="store_true",
                             help="Save extracted audio file")
    output_group.add_argument("-v", "--output-video", 
                             action="store_true",
                             help="Generate video with embedded subtitles")
    
    # Language and translation
    lang_group = parser.add_argument_group('Language Options')
    lang_group.add_argument("--language", 
                           type=str, 
                           default=None,
                           help="Force audio language (e.g., en, fr, tr)")
    lang_group.add_argument("--translate-off", 
                           action="store_true",
                           help="Disable translation (output original language)")
    lang_group.add_argument("--translate-to", 
                           default="tr",
                           help="Target language for translation (default: %(default)s)")
    
    # Performance settings
    perf_group = parser.add_argument_group('Performance Options')
    perf_group.add_argument("--batch-size", 
                           type=int, 
                           default=10,
                           help="Segments per translation batch (default: %(default)s)")
    perf_group.add_argument("--max-workers", 
                           type=int, 
                           default=4,
                           help="Max parallel translation threads (default: %(default)s)")
    perf_group.add_argument("--extract-workers", 
                           type=int, 
                           default=max(1, psutil.cpu_count(logical=False)//2),
                           help="Audio extraction workers (default: half of CPU cores)")
    
    # Advanced options
    adv_group = parser.add_argument_group('Advanced Options')
    adv_group.add_argument("--device", 
                          default="cuda" if is_available() else "cpu",
                          help="Processing device (default: %(default)s)")
    adv_group.add_argument("--verbose", 
                          action="store_true",
                          help="Show detailed processing logs")
    adv_group.add_argument("--enhance-consistency", 
                          action="store_true",
                          help="Improve transcription consistency")

    args = parser.parse_args()
    
    # Validate and resolve paths
    input_paths = []
    for pattern in args.paths:
        input_paths.extend(glob.glob(pattern))
    
    if not input_paths:
        print("Error: No valid input files found!")
        return

    # Handle .en models
    if args.model.endswith(".en"):
        args.language = "en"
        warnings.warn("Forcing English transcription")

    # Initialize model
    try:
        model = stable_whisper.load_model(args.model, device=args.device)
    except Exception as e:
        print(f"Model loading failed: {str(e)}")
        return

    # Process files
    audio_paths = get_audio(
        input_paths,
        args.output_audio,
        args.output_dir,
        args.extract_workers
    )

    subtitles = generate_subtitles(
        audio_paths,
        args.output_srt,
        args.output_dir,
        model,
        args
    )

    if args.output_video:
        create_subtitled_videos(
            input_paths,
            subtitles,
            args.output_dir
        )

def get_audio(paths, save_audio, output_dir, num_workers):
    audio_map = {}
    tasks = []
    
    for path in paths:
        if is_audio(path):
            audio_map[path] = path
            continue
            
        target_dir = output_dir if save_audio else tempfile.gettempdir()
        output_path = os.path.join(target_dir, f"{get_filename(path)}.mp3")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        tasks.append((path, output_path))
        audio_map[path] = output_path
    
    if tasks:
        with multiprocessing.Pool(num_workers) as pool:
            pool.starmap(ffmpeg_extract_audio, tasks)
    
    return audio_map

def generate_subtitles(audio_paths, output_srt, output_dir, model, args):
    subtitles = {}
    
    for path, audio_path in audio_paths.items():
        print(f"\nProcessing: {os.path.basename(path)}")
        
        try:
            result = model.transcribe(
                audio_path,
                language=args.language,
                verbose=args.verbose,
                condition_on_previous_text=args.enhance_consistency
            )
        except Exception as e:
            print(f"Transcription failed: {str(e)}")
            continue
        
        srt_filename = f"{get_filename(path)}.srt"
        srt_path = os.path.join(output_dir, srt_filename) if output_srt else os.path.join(os.getcwd(), srt_filename)
        os.makedirs(os.path.dirname(srt_path), exist_ok=True)
        
        try:
            with open(srt_path, "w", encoding="utf-8") as f:
                write_srt(
                    result,
                    f,
                    translate_off=args.translate_off,
                    translate_to=args.translate_to,
                    batch_size=args.batch_size,
                    max_workers=args.max_workers
                )
            subtitles[path] = srt_path
            print(f"Subtitles saved to: {os.path.abspath(srt_path)}")
        except Exception as e:
            print(f"File write error: {str(e)}")
    
    return subtitles

def create_subtitled_videos(input_paths, subtitles, output_dir):
    for path in input_paths:
        if is_audio(path) or path not in subtitles:
            continue
            
        output_filename = f"{get_filename(path)}_subtitled.mp4"
        output_path = os.path.join(output_dir, output_filename)
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"\nCreating subtitled video: {os.path.abspath(output_path)}")
        
        try:
            video = ffmpeg.input(path)
            audio = video.audio
            
            ffmpeg.output(
                video.filter('subtitles', subtitles[path]),
                audio,
                output_path,
                vcodec='libx264',
                acodec='copy'
            ).run(overwrite_output=True, quiet=True)
            
            print("Video creation completed successfully!")
        except Exception as e:
            print(f"Video processing failed: {str(e)}")

if __name__ == "__main__":
    main()
